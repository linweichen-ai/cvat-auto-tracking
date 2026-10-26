/* MV3 扩展桥接：sessionid 是 HttpOnly，content script 读不到，
 * 通过 background service worker 的 chrome.cookies API 读取。 */
(function () {
  'use strict';
  window.__CVAT_PANEL_BRIDGE__ = {
    getCookiesDoc: function () { return document.cookie || ''; },
    getCredentials: function () {
      return new Promise(function (resolve) {
        try {
          chrome.runtime.sendMessage({ type: 'CP_GET_COOKIES' }, function (resp) {
            if (chrome.runtime.lastError || !resp) return resolve({});
            resolve({ sessionid: resp.sessionid || '', csrftoken: resp.csrftoken || '' });
          });
        } catch (e) { resolve({}); }
      });
    },
  };
})();

/* ============================================================================
 * CVAT 自助标注面板（SAM3 自动跟踪）— 面板核心逻辑
 * 本文件不单独运行，由 bridge（userscript / 扩展 content script）先设置
 * window.__CVAT_PANEL_BRIDGE__ 后再加载。
 *
 * bridge 需提供:
 *   getCredentials: async () => { sessionid: string, csrftoken: string }
 *     —— sessionid 是 HttpOnly，userscript 走 GM_cookie，扩展走 chrome.cookies。
 *   getCookiesDoc:  () => string   —— document.cookie（可读 csrftoken）
 *   request:        (url, options) => fetch-like（默认可省略，内部用 fetch）
 * ========================================================================== */
(function () {
  'use strict';
  if (window.__CVAT_PANEL_MOUNTED__) return;
  window.__CVAT_PANEL_MOUNTED__ = true;

  var BRIDGE = window.__CVAT_PANEL_BRIDGE__ || {};
  var CVAT_URL = location.protocol + '//' + location.host; // http://172.16.88.68:12080

  // ------------------------------------------------------------------ state
  var state = {
    jobId: null,
    frame: null,            // 页面识别到的当前帧
    frameManual: null,      // 用户手动覆盖的帧号
    labels: [],
    shapes: [],
    busy: false,
    cacheTimer: null,
    sam3Base: localStorage.getItem('sam3-panel-base') || 'http://172.16.88.68:11089',
    cred: { sessionid: '', csrftoken: '' },
  };

  var els = {};
  var ICON_DRAG = '⠿';

  // ------------------------------------------------------------------ utils
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    for (var k in attrs) {
      if (k === 'class') node.className = attrs[k];
      else if (k === 'text') node.textContent = attrs[k];
      else if (k === 'html') node.innerHTML = attrs[k];
      else node.setAttribute(k, attrs[k]);
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function escapeHtml(v) {
    return String(v).replace(/[&<>'"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c];
    });
  }

  function nowTime() {
    return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function setStatus(stage, msg, tone) {
    tone = tone || 'normal';
    if (!els.statusStage) return;
    els.statusStage.textContent = stage;
    els.statusMsg.textContent = msg;
    els.statusStage.style.color = tone === 'error' ? '#ff8a8a' : tone === 'success' ? '#7ddba8' : '#e6c565';
    els.statusTime.textContent = nowTime();
  }

  function setBusy(v) {
    state.busy = v;
    document.querySelectorAll('#cvat-sam3-panel button').forEach(function (b) {
      if (!b.classList.contains('cp-notouch')) b.disabled = v;
    });
  }

  function toast(text, isError) {
    var t = el('div', { class: 'cp-toast' + (isError ? ' cp-err' : ''), text: text });
    document.body.appendChild(t);
    setTimeout(function () { t.classList.add('cp-show'); }, 10);
    setTimeout(function () { t.classList.remove('cp-show'); setTimeout(function () { t.remove(); }, 300); }, 3600);
  }

  // ------------------------------------------------------------------ API
  function sam3Request(endpoint, payload, timeoutMs) {
    timeoutMs = timeoutMs || 900000;
    return new Promise(function (resolve, reject) {
      var controller = ('AbortController' in window) ? new AbortController() : null;
      var timer = setTimeout(function () {
        if (controller) controller.abort();
        reject(new Error('请求超时（' + Math.round(timeoutMs / 1000) + 's），后端仍在处理可稍后重试'));
      }, timeoutMs);
      fetch(state.sam3Base + endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CVAT-URL': CVAT_URL,
          'X-CVAT-Session-ID': state.cred.sessionid,
          'X-CVAT-CSRF-Token': state.cred.csrftoken,
        },
        body: JSON.stringify(payload || {}),
        signal: controller ? controller.signal : undefined,
      }).then(function (r) {
        return r.text().then(function (txt) {
          clearTimeout(timer);
          var body;
          try { body = JSON.parse(txt); } catch (e) { body = { raw: txt }; }
          if (!r.ok) {
            var msg = (body && (body.detail || body.message || body.error)) || txt || ('HTTP ' + r.status);
            throw new Error(endpoint + ' 失败：' + msg);
          }
          resolve(body);
        });
      }).catch(function (e) { clearTimeout(timer); reject(e); });
    });
  }

  function requireContext(opts) {
    opts = opts || {};
    if (!Number.isInteger(state.jobId) || state.jobId <= 0) throw new Error('请先打开一个标注作业页（URL 含 /tasks/<id>/jobs/<id>）');
    var out = {
      jobId: state.jobId,
      labelId: Number(els.labelSelect.value),
      shapeId: Number(els.shapeSelect.value) || null,
      startFrame: Number(els.rangeStart.value),
      endFrame: Number(els.rangeEnd.value),
      currentFrame: currentFrame(),
    };
    if (opts.label && (!Number.isInteger(out.labelId) || out.labelId <= 0)) throw new Error('请先选择标签');
    if (opts.range && (!Number.isInteger(out.startFrame) || !Number.isInteger(out.endFrame) || out.startFrame < 0 || out.endFrame < out.startFrame)) {
      throw new Error('帧范围无效：结束帧必须 ≥ 起始帧');
    }
    return out;
  }

  function currentFrame() {
    if (Number.isInteger(state.frameManual) && state.frameManual >= 0) return state.frameManual;
    return state.frame;
  }

  // ------------------------------------------------------------------ 凭据
  function readCsrfFromDoc() {
    var m = (BRIDGE.getCookiesDoc ? BRIDGE.getCookiesDoc() : document.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function refreshCredentials() {
    state.cred.csrftoken = readCsrfFromDoc() || state.cred.csrftoken;
    try {
      var cred = await BRIDGE.getCredentials();
      if (cred && cred.sessionid) state.cred.sessionid = cred.sessionid;
      if (cred && cred.csrftoken) state.cred.csrftoken = cred.csrftoken;
    } catch (e) { /* 手动输入兜底 */ }
    if (!state.cred.sessionid) {
      setConn('session', 'red', '无会话');
      return false;
    }
    try {
      var r = await sam3Request('/api/auth_status', {}, 15000);
      if (r.authenticated) {
        setConn('session', 'green', 'CVAT:' + (r.username || '已认证'));
        setConn('sam', 'green', 'SAM3:已连接');
        return true;
      }
      setConn('session', 'red', 'CVAT:未认证');
    } catch (e) {
      setConn('session', 'red', 'CVAT:会话失效');
      setConn('sam', 'red', 'SAM3:不通');
    }
    return false;
  }

  function setConn(which, color, text) {
    var node = which === 'session' ? els.connSession : els.connSam;
    node.style.color = color === 'green' ? '#7ddba8' : color === 'red' ? '#ff8a8a' : '#aaa';
    node.textContent = text;
  }

  // ------------------------------------------------------------------ 标签/实例
  async function loadLabels() {
    if (!state.jobId) return;
    try {
      var r = await sam3Request('/api/get_labels', { job_id: state.jobId }, 15000);
      state.labels = Array.isArray(r.labels) ? r.labels : [];
      var prev = localStorage.getItem('sam3-label-' + state.jobId) || '';
      els.labelSelect.innerHTML = state.labels.length
        ? state.labels.map(function (l) { return '<option value="' + l.id + '">' + escapeHtml(l.name) + ' · ' + l.id + '</option>'; }).join('')
        : '<option value="">（无标签）</option>';
      els.targetLabel.innerHTML = '<option value="">选择新标签</option>' + els.labelSelect.innerHTML;
      if (prev && state.labels.some(function (l) { return String(l.id) === prev; })) els.labelSelect.value = prev;
      setStatus('就绪', '已加载 ' + state.labels.length + ' 个标签');
      refreshShapes(false);
    } catch (e) { setStatus('标签加载失败', e.message, 'error'); }
  }

  async function refreshShapes(showMsg) {
    if (!state.jobId) return;
    var fid = currentFrame();
    if (!Number.isInteger(fid)) return;
    try {
      var labelId = Number(els.labelSelect.value);
      if (!labelId) { els.shapeSelect.innerHTML = '<option value="">全部（按标签）</option>'; return; }
      var r = await sam3Request('/api/get_shapes', { job_id: state.jobId, frame_index: fid, label_id: labelId }, 20000);
      state.shapes = Array.isArray(r.shapes) ? r.shapes : [];
      els.shapeSelect.innerHTML = '<option value="">全部（按标签）</option>' + state.shapes.map(function (s) {
        return '<option value="' + s.id + '">#' + s.id + ' ' + escapeHtml(s.type || s.desc || '') + '</option>';
      }).join('');
      if (showMsg) setStatus('实例已同步', '第 ' + fid + ' 帧找到 ' + state.shapes.length + ' 个实例');
    } catch (e) {
      els.shapeSelect.innerHTML = '<option value="">全部（按标签）</option>';
      if (showMsg) setStatus('实例同步失败', e.message, 'error');
    }
  }

  // ------------------------------------------------------------------ 缓存
  function pollCache(jobId, startFrame, endFrame) {
    clearInterval(state.cacheTimer);
    var check = function () {
      sam3Request('/api/cache_status', { job_id: jobId, start_frame: startFrame, end_frame: endFrame }, 15000).then(function (r) {
        var total = Number(r.total) || (endFrame - startFrame + 1);
        var loaded = Number(r.loaded) || 0;
        var pct = r.range_ready ? 100 : Math.min(100, Math.round(loaded / total * 100));
        els.cacheBar.style.width = pct + '%';
        els.cacheDetail.textContent = r.range_ready ? (total + '/' + total + ' 已就绪') : (loaded + '/' + total);
        if (r.range_ready) {
          clearInterval(state.cacheTimer); state.cacheTimer = null;
          setStatus('缓存完成', '第 ' + startFrame + '-' + endFrame + ' 帧已就绪，可以开始跟踪', 'success');
          toast('帧缓存完成');
        }
      }).catch(function (e) {
        clearInterval(state.cacheTimer); state.cacheTimer = null;
        setStatus('缓存状态读取失败', e.message, 'error');
      });
    };
    check();
    state.cacheTimer = setInterval(check, 1500);
  }

  async function startCache() {
    var ctx = requireContext({ range: true });
    setStatus('帧缓存', '正在请求缓存第 ' + ctx.startFrame + '-' + ctx.endFrame + ' 帧…');
    var r = await sam3Request('/api/cache_frames', { job_id: ctx.jobId, start_frame: ctx.startFrame, end_frame: ctx.endFrame });
    if (r.status === 'ready') {
      els.cacheBar.style.width = '100%';
      els.cacheDetail.textContent = '范围已就绪';
      setStatus('缓存已就绪', r.message || '无需重复缓存', 'success');
    } else {
      pollCache(ctx.jobId, ctx.startFrame, ctx.endFrame);
    }
  }

  // ------------------------------------------------------------------ 跟踪
  async function trackRange() {
    var ctx = requireContext({ label: true, range: true });
    if (!confirm('开始前请确认已在 CVAT 中 Ctrl+S 保存当前标注。\n将对第 ' + ctx.startFrame + ' 帧的实例逐一生成到第 ' + ctx.endFrame + ' 帧，继续？')) return;

    // 确定要跟踪的实例列表
    var seeds;
    if (ctx.shapeId) {
      seeds = [{ id: ctx.shapeId, label_id: ctx.labelId }];
    } else {
      setStatus('读取实例', '正在读取第 ' + ctx.startFrame + ' 帧的标注实例…');
      var r = await sam3Request('/api/get_shapes', { job_id: ctx.jobId, frame_index: ctx.startFrame, label_id: ctx.labelId }, 20000);
      seeds = (r.shapes || []).map(function (s) { return { id: s.id, label_id: ctx.labelId }; });
      if (!seeds.length) throw new Error('第 ' + ctx.startFrame + ' 帧没有该标签的实例，请先手动标注前几帧');
    }

    var total = seeds.length;
    var done = 0, generatedTotal = 0, failed = [];
    for (var i = 0; i < seeds.length; i++) {
      var seed = seeds[i];
      setStatus('范围跟踪中', '实例 ' + (i + 1) + '/' + total + '（shape #' + seed.id + '）…');
      try {
        var res = await sam3Request('/api/propagate_shape', {
          job_id: ctx.jobId, start_frame: ctx.startFrame, end_frame: ctx.endFrame,
          label_id: seed.label_id, shape_id: seed.id,
        });
        done++;
        generatedTotal += Number(res.generated) || 0;
      } catch (e) {
        failed.push('#' + seed.id + ': ' + e.message);
      }
    }
    els.trackResult.textContent = '完成 ' + done + '/' + total + '，共生成约 ' + generatedTotal + ' 个形状' + (failed.length ? '；失败 ' + failed.length + ' 个' : '');
    if (failed.length) {
      setStatus('范围跟踪部分失败', failed.join('；'), 'error');
      toast('部分实例跟踪失败，详见状态行', true);
    } else {
      setStatus('范围跟踪完成', '生成约 ' + generatedTotal + ' 个形状，点“跳转结束帧”检查', 'success');
      toast('范围跟踪完成！');
    }
  }

  async function trackNext() {
    var ctx = requireContext({ label: true });
    if (!Number.isInteger(ctx.currentFrame)) throw new Error('无法识别当前帧，请先在 CVAT 切换一次帧');
    if (!confirm('请确认已 Ctrl+S 保存。将把当前选中实例从第 ' + ctx.currentFrame + ' 帧传播到第 ' + (ctx.currentFrame + 1) + ' 帧，继续？')) return;
    var res;
    if (ctx.shapeId) {
      res = await sam3Request('/api/propagate_shape', {
        job_id: ctx.jobId, start_frame: ctx.currentFrame, end_frame: ctx.currentFrame + 1,
        label_id: ctx.labelId, shape_id: ctx.shapeId,
      });
    } else {
      res = await sam3Request('/api/propagate_next', { job_id: ctx.jobId, mask_frame_index: ctx.currentFrame, object_id: ctx.labelId });
    }
    setStatus('传播完成', '生成 ' + (res.generated || 0) + ' 个形状，刷新后可见', 'success');
    toast('已传播到下一帧，刷新页面后可见');
  }

  async function deleteRange() {
    var ctx = requireContext({ label: true, range: true });
    if (!confirm('【二次确认】将删除第 ' + ctx.startFrame + '-' + ctx.endFrame + ' 帧内标签「' + labelName(ctx.labelId) + '」的全部 shape，且不可撤销，继续？')) return;
    var res = await sam3Request('/api/delete_range', { job_id: ctx.jobId, start_frame: ctx.startFrame, end_frame: ctx.endFrame, object_id: ctx.labelId });
    setStatus('删除完成', '已删除 ' + (res.deleted || 0) + ' 个标注', 'success');
  }

  async function changeLabelRange() {
    var ctx = requireContext({ label: true, range: true });
    var tgt = Number(els.targetLabel.value);
    if (!Number.isInteger(tgt) || tgt <= 0 || tgt === ctx.labelId) throw new Error('请选择一个不同的新标签');
    if (!confirm('【二次确认】将把第 ' + ctx.startFrame + '-' + ctx.endFrame + ' 帧内标签「' + labelName(ctx.labelId) + '」全部改为「' + labelName(tgt) + '」，继续？')) return;
    var res = await sam3Request('/api/change_label_range', {
      job_id: ctx.jobId, start_frame: ctx.startFrame, end_frame: ctx.endFrame,
      object_src: ctx.labelId, object_tgt: tgt,
    });
    setStatus('改标签完成', '已更新 ' + (res.updated || 0) + ' 个标注', 'success');
  }

  function labelName(id) {
    var f = state.labels.filter(function (l) { return l.id === id; })[0];
    return f ? f.name : ('#' + id);
  }

  async function undoLast() {
    var ctx = requireContext({ label: false });
    await sam3Request('/api/undo_last', { job_id: ctx.jobId }, 30000);
    setStatus('已撤销', '后端已撤销上一次写操作', 'success');
  }

  // ------------------------------------------------------------------ 提示分割（坐标输入，二期可叠加画布取点）
  async function doSegment() {
    var ctx = requireContext({ label: true });
    var fid = currentFrame();
    var mode = els.segMode.value;
    var fmt = els.segFormat.value || 'polygon';
    if (mode === 'text') {
      var txt = els.segText.value.trim();
      if (!txt) throw new Error('请输入目标描述文本');
      if (!confirm('请确认已 Ctrl+S 保存。将用文本提示在第 ' + fid + ' 帧生成「' + labelName(ctx.labelId) + '」，继续？')) return;
      var r1 = await sam3Request('/api/segment_text', {
        job_id: ctx.jobId, mask_frame_index: fid, text_prompt: txt, object_id: ctx.labelId, output_format: fmt,
      });
      setStatus('文本分割完成', '生成 ' + (r1.generated || 0) + ' 个形状，刷新后可见', 'success');
      return;
    }
    // point / box：从 textarea 解析坐标
    var lines = els.segCoords.value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
    if (!lines.length) throw new Error('请先在坐标框输入提示点/框，每行一个');
    var prompts = [];
    for (var i = 0; i < lines.length; i++) {
      var nums = lines[i].split(/[\s,，]+/).map(Number);
      if (mode === 'point') {
        if (nums.length < 2 || nums.some(isNaN)) throw new Error('第 ' + (i + 1) + ' 行点格式应为: x,y');
        prompts.push({ type: 'point', points: [[nums[0], nums[1]]] });
      } else {
        if (nums.length < 4 || nums.some(isNaN)) throw new Error('第 ' + (i + 1) + ' 行框格式应为: x1,y1,x2,y2');
        prompts.push({ type: 'box', points: [nums[0], nums[1], nums[2], nums[3]] });
      }
    }
    if (!confirm('请确认已 Ctrl+S 保存。将在第 ' + fid + ' 帧用 ' + prompts.length + ' 个' + (mode === 'point' ? '点' : '框') + '提示分割「' + labelName(ctx.labelId) + '」，继续？')) return;
    var r2 = await sam3Request('/api/segment_prompt', {
      job_id: ctx.jobId, frame_index: fid, label_id: ctx.labelId, prompts: prompts, output_format: fmt,
    });
    setStatus('提示分割完成', '生成 ' + (r2.generated || 0) + ' 个形状，刷新后可见', 'success');
  }

  // ------------------------------------------------------------------ CVAT 帧识别 / 跳转
  function detectFrameFromDom() {
    var inputs = document.querySelectorAll('input');
    for (var i = 0; i < inputs.length; i++) {
      var inp = inputs[i];
      if (inp.disabled || inp.type === 'hidden') continue;
      var v = (inp.value || '').trim();
      if (!/^\d+$/.test(v)) continue;
      var player = inp.closest('[class*="player" i], [class*="frame" i]');
      if (player) {
        var n = Number(v);
        if (n >= 0 && n < 1000000) return n;
      }
    }
    return null;
  }

  function pollFrame() {
    var f = detectFrameFromDom();
    if (Number.isInteger(f)) {
      if (f !== state.frame) {
        var prev = state.frame;
        state.frame = f;
        if (els.frameVal) els.frameVal.textContent = f;
        // 帧变化时刷新实例下拉
        if (Number.isInteger(prev) && prev !== f && !state.busy) refreshShapes(false);
      }
    }
  }

  function jumpToFrame(n) {
    if (!confirm('将刷新 CVAT 页面到第 ' + n + ' 帧查看结果。\n请确认当前标注已 Ctrl+S 保存，继续？')) return;
    var url = new URL(location.href);
    url.searchParams.set('frame', String(n));
    location.href = url.toString();
  }

  function parseJobFromUrl() {
    var m = location.pathname.match(/\/tasks\/\d+\/jobs\/(\d+)/) || location.pathname.match(/\/jobs\/(\d+)/);
    return m ? Number(m[1]) : null;
  }

  async function onUrlChanged() {
    var jid = parseJobFromUrl();
    if (jid && jid !== state.jobId) {
      state.jobId = jid;
      if (els.jobVal) els.jobVal.textContent = jid;
      await refreshCredentials();
      await loadLabels();
    }
  }

  // ------------------------------------------------------------------ 面板 UI
  function buildPanel() {
    var css = el('style', { text: PANEL_CSS });
    document.head.appendChild(css);

    var panel = el('div', { id: 'cvat-sam3-panel' });

    // 标题栏
    var title = el('div', { class: 'cp-title' }, [
      el('span', { class: 'cp-grip cp-notouch', text: ICON_DRAG }),
      el('span', { text: 'SAM3 自助标注' }),
      el('span', { class: 'cp-spacer' }),
      el('button', { class: 'cp-min cp-notouch', text: '—' }),
    ]);
    var connLine = el('div', { class: 'cp-conn' }, [
      (els.connSession = el('span', { class: 'cp-dot', text: 'CVAT:…' })),
      (els.connSam = el('span', { class: 'cp-dot', text: 'SAM3:…' })),
    ]);

    // 上下文
    var ctxRow = el('div', { class: 'cp-row' }, [
      el('label', { text: 'Job ' }), (els.jobVal = el('b', { text: '—' })),
      el('label', { text: '  帧 ' }), (els.frameVal = el('b', { text: '—' })),
    ]);
    var frameManualRow = el('div', { class: 'cp-row' }, [
      el('label', { text: '手动帧:' }),
      (els.frameManual = el('input', { type: 'number', min: '0', style: 'width:64px' })),
      el('button', { class: 'cp-mini', text: '应用/同步' }),
    ]);

    // 凭据手动兜底
    var credRow = el('details', { class: 'cp-adv' }, [
      el('summary', { text: '连接/凭据（一般不用动）' }),
      el('div', { class: 'cp-row' }, [
        el('label', { text: '后端:' }),
        (els.samBase = el('input', { value: state.sam3Base, style: 'width:170px' })),
        el('button', { class: 'cp-mini', text: '保存' }),
      ]),
      el('div', { class: 'cp-row' }, [
        el('label', { text: 'sessionid:' }),
        (els.sessionInput = el('input', { placeholder: '自动获取；失败时从 F12→Cookie 复制', style: 'width:200px' })),
      ]),
      el('div', { class: 'cp-row' }, [
        el('button', { class: 'cp-mini', text: '重新检测连接' }),
      ]),
    ]);

    // 标签/实例
    var labelRow = el('div', { class: 'cp-row' }, [
      el('label', { text: '标签:' }), (els.labelSelect = el('select', { style: 'flex:1' })),
    ]);
    var shapeRow = el('div', { class: 'cp-row' }, [
      el('label', { text: '实例:' }), (els.shapeSelect = el('select', { style: 'flex:1' })),
      el('button', { class: 'cp-mini cp-refresh-shapes', text: '↻' }),
    ]);

    // 范围 + 缓存
    var rangeRow = el('div', { class: 'cp-row' }, [
      el('label', { text: '帧范围:' }),
      (els.rangeStart = el('input', { type: 'number', min: '0', value: '0', style: 'width:60px' })),
      el('span', { text: '→' }),
      (els.rangeEnd = el('input', { type: 'number', min: '0', value: '3', style: 'width:60px' })),
      el('button', { class: 'cp-mini cp-set-start', text: '起点=当前' }),
      el('button', { class: 'cp-mini cp-set-end', text: '终点=当前' }),
    ]);
    var cacheRow = el('div', { class: 'cp-row' }, [
      (els.cacheBar = el('div', { class: 'cp-bar' }, [el('div', { class: 'cp-bar-fill' })])),
      (els.cacheDetail = el('span', { class: 'cp-cache-detail', text: '未缓存' })),
    ]);
    var cacheBtns = el('div', { class: 'cp-row' }, [
      el('button', { class: 'cp-btn cp-cache', text: '① 缓存帧' }),
      el('button', { class: 'cp-btn cp-clear-cache', text: '清缓存' }),
    ]);

    // 核心跟踪
    var trackBtns = el('div', { class: 'cp-row' }, [
      el('button', { class: 'cp-btn cp-primary cp-track-range', text: '② 范围自动跟踪' }),
      el('button', { class: 'cp-btn cp-track-next', text: '传播下一帧' }),
    ]);
    els.trackResult = el('div', { class: 'cp-track-result', text: '' });
    var jumpRow = el('div', { class: 'cp-row' }, [
      el('button', { class: 'cp-btn cp-jump', text: '③ 跳转结束帧检查' }),
      el('button', { class: 'cp-btn cp-undo', text: '撤销上一步' }),
    ]);

    // 标注维护
    var maintRow = el('div', { class: 'cp-row' }, [
      el('label', { text: '改标签→' }), (els.targetLabel = el('select', { style: 'flex:1' })),
      el('button', { class: 'cp-btn cp-change-label', text: '改标签' }),
      el('button', { class: 'cp-btn cp-danger cp-delete', text: '删范围' }),
    ]);

    // 提示分割
    var segBox = el('details', { class: 'cp-adv' }, [
      el('summary', { text: '提示分割（点/框/文本，二期画布取点）' }),
      el('div', { class: 'cp-row' }, [
        el('label', { text: '方式:' }),
        (els.segMode = el('select', {}, [
          el('option', { value: 'point', text: '点 point' }),
          el('option', { value: 'box', text: '框 box' }),
          el('option', { value: 'text', text: '文本 text' }),
        ])),
        el('label', { text: '输出:' }),
        (els.segFormat = el('select', {}, [
          el('option', { value: 'polygon', text: 'polygon' }),
          el('option', { value: 'mask', text: 'mask' }),
        ])),
      ]),
      (els.segCoords = el('textarea', { class: 'cp-coords', placeholder: '每行一个坐标：\n点: x,y\n框: x1,y1,x2,y2' })),
      (els.segText = el('input', { class: 'cp-coords', placeholder: '文本提示，如: pallet on the floor' })),
      el('div', { class: 'cp-row' }, [el('button', { class: 'cp-btn cp-seg', text: '执行分割' })]),
    ]);

    // 状态栏
    var statusBar = el('div', { class: 'cp-status' }, [
      (els.statusStage = el('span', { class: 'cp-status-stage', text: '初始化' })),
      (els.statusMsg = el('span', { class: 'cp-status-msg', text: '' })),
      (els.statusTime = el('span', { class: 'cp-status-time', text: '' })),
    ]);

    panel.appendChild(title);
    panel.appendChild(connLine);
    panel.appendChild(ctxRow);
    panel.appendChild(frameManualRow);
    panel.appendChild(credRow);
    panel.appendChild(labelRow);
    panel.appendChild(shapeRow);
    panel.appendChild(rangeRow);
    panel.appendChild(cacheRow);
    panel.appendChild(cacheBtns);
    panel.appendChild(trackBtns);
    panel.appendChild(els.trackResult);
    panel.appendChild(jumpRow);
    panel.appendChild(maintRow);
    panel.appendChild(segBox);
    panel.appendChild(statusBar);
    document.body.appendChild(panel);

    // 折叠
    title.querySelector('.cp-min').addEventListener('click', function () {
      panel.classList.toggle('cp-collapsed');
    });
    // 拖动
    makeDraggable(panel, title);

    // 事件绑定
    panel.querySelector('.cp-set-start').addEventListener('click', function () { if (Number.isInteger(currentFrame())) els.rangeStart.value = currentFrame(); });
    panel.querySelector('.cp-set-end').addEventListener('click', function () { if (Number.isInteger(currentFrame())) els.rangeEnd.value = currentFrame(); });
    panel.querySelector('.cp-refresh-shapes').addEventListener('click', function () { refreshShapes(true); });
    els.labelSelect.addEventListener('change', function () {
      localStorage.setItem('sam3-label-' + state.jobId, els.labelSelect.value);
      refreshShapes(false);
    });
    frameManualRow.querySelector('button').addEventListener('click', function () {
      var v = Number(els.frameManual.value);
      state.frameManual = Number.isInteger(v) && v >= 0 ? v : null;
      els.frameVal.textContent = Number.isInteger(state.frameManual) ? state.frameManual + '(手动)' : '—';
      refreshShapes(false);
    });

    credRow.querySelector('button').addEventListener('click', async function () {
      state.sam3Base = els.samBase.value.trim() || state.sam3Base;
      localStorage.setItem('sam3-panel-base', state.sam3Base);
      if (els.sessionInput.value.trim()) state.cred.sessionid = els.sessionInput.value.trim();
      await refreshCredentials();
    });
    credRow.querySelectorAll('button')[1].addEventListener('click', async function () {
      await refreshCredentials();
    });

    panel.querySelector('.cp-cache').addEventListener('click', function () { runBusy('帧缓存', startCache); });
    panel.querySelector('.cp-clear-cache').addEventListener('click', function () {
      runBusy('清缓存', async function () {
        var ctx = requireContext({ label: false });
        await sam3Request('/api/clear_cache', { job_id: ctx.jobId }, 30000);
        els.cacheBar.firstChild.style.width = '0%';
        els.cacheDetail.textContent = '已清除';
        setStatus('缓存已清除', '已清空后端帧缓存', 'success');
      });
    });
    panel.querySelector('.cp-track-range').addEventListener('click', function () { runBusy('范围跟踪', trackRange); });
    panel.querySelector('.cp-track-next').addEventListener('click', function () { runBusy('单帧传播', trackNext); });
    panel.querySelector('.cp-jump').addEventListener('click', function () {
      var n = Number(els.rangeEnd.value);
      if (!Number.isInteger(n)) { toast('请先填写结束帧', true); return; }
      jumpToFrame(n);
    });
    panel.querySelector('.cp-undo').addEventListener('click', function () { runBusy('撤销', undoLast); });
    panel.querySelector('.cp-delete').addEventListener('click', function () { runBusy('删除标注', deleteRange); });
    panel.querySelector('.cp-change-label').addEventListener('click', function () { runBusy('更改标签', changeLabelRange); });
    panel.querySelector('.cp-seg').addEventListener('click', function () { runBusy('提示分割', doSegment); });
  }

  function makeDraggable(panel, handle) {
    var sx, sy, ox, oy, dragging = false;
    handle.style.cursor = 'move';
    handle.addEventListener('mousedown', function (e) {
      dragging = true;
      var rect = panel.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = rect.left; oy = rect.top;
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      panel.style.left = (ox + e.clientX - sx) + 'px';
      panel.style.top = (oy + e.clientY - sy) + 'px';
      panel.style.right = 'auto';
    });
    document.addEventListener('mouseup', function () { dragging = false; });
  }

  async function runBusy(stage, task) {
    if (state.busy) { toast('有任务进行中，请稍候'); return; }
    setBusy(true);
    setStatus(stage, '正在提交请求，请勿关闭页面…');
    try {
      await task();
    } catch (e) {
      setStatus('操作失败', e.message || String(e), 'error');
      toast(e.message || String(e), true);
    } finally {
      setBusy(false);
    }
  }

  // ------------------------------------------------------------------ CSS
  var PANEL_CSS = `
  #cvat-sam3-panel{position:fixed;top:80px;right:16px;width:330px;max-height:85vh;overflow-y:auto;z-index:2147483646;
    background:#1e2330;color:#e8ebf2;font:12px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;
    border:1px solid #3a4157;border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.45);}
  #cvat-sam3-panel .cp-title{display:flex;align-items:center;gap:6px;padding:8px 10px;background:#2a3040;border-radius:10px 10px 0 0;font-weight:600;}
  #cvat-sam3-panel .cp-grip{cursor:move;color:#8892ab;}
  #cvat-sam3-panel .cp-spacer{flex:1;}
  #cvat-sam3-panel .cp-min{background:none;border:none;color:#cfd6e6;font-size:14px;cursor:pointer;}
  #cvat-sam3-panel .cp-conn{display:flex;gap:10px;padding:4px 10px;font-size:11px;border-bottom:1px solid #333a4e;}
  #cvat-sam3-panel .cp-row{display:flex;align-items:center;gap:6px;padding:4px 10px;}
  #cvat-sam3-panel .cp-row label{white-space:nowrap;color:#9aa3bb;}
  #cvat-sam3-panel input,#cvat-sam3-panel select{background:#141824;color:#e8ebf2;border:1px solid #3a4157;border-radius:5px;padding:3px 5px;font-size:12px;}
  #cvat-sam3-panel .cp-btn{background:#33405e;color:#e8ebf2;border:1px solid #4a5878;border-radius:6px;padding:5px 8px;cursor:pointer;font-size:12px;flex:1;}
  #cvat-sam3-panel .cp-btn:hover{background:#3d4c70;}
  #cvat-sam3-panel .cp-primary{background:#2f6f4f;border-color:#3f9969;}
  #cvat-sam3-panel .cp-primary:hover{background:#37855d;}
  #cvat-sam3-panel .cp-danger{background:#7a3540;border-color:#a04855;}
  #cvat-sam3-panel .cp-mini{background:#2a3040;color:#cfd6e6;border:1px solid #3a4157;border-radius:5px;padding:2px 6px;cursor:pointer;font-size:11px;}
  #cvat-sam3-panel .cp-bar{flex:1;height:10px;background:#141824;border:1px solid #3a4157;border-radius:5px;overflow:hidden;}
  #cvat-sam3-panel .cp-bar-fill{height:100%;width:0;background:linear-gradient(90deg,#2f6f4f,#4fae7c);transition:width .3s;}
  #cvat-sam3-panel .cp-cache-detail{font-size:11px;color:#9aa3bb;min-width:56px;}
  #cvat-sam3-panel .cp-track-result{padding:2px 10px;font-size:11px;color:#7ddba8;min-height:14px;}
  #cvat-sam3-panel .cp-status{display:flex;flex-direction:column;gap:2px;padding:6px 10px;background:#181c28;border-top:1px solid #333a4e;border-radius:0 0 10px 10px;font-size:11px;}
  #cvat-sam3-panel .cp-status-stage{font-weight:600;}
  #cvat-sam3-panel .cp-status-msg{color:#aab2c8;word-break:break-all;}
  #cvat-sam3-panel .cp-status-time{color:#66708c;}
  #cvat-sam3-panel .cp-adv{padding:4px 10px;border-top:1px dashed #333a4e;}
  #cvat-sam3-panel .cp-adv summary{cursor:pointer;color:#9aa3bb;}
  #cvat-sam3-panel .cp-coords{width:100%;min-height:46px;box-sizing:border-box;margin:4px 0;background:#141824;color:#e8ebf2;border:1px solid #3a4157;border-radius:5px;padding:4px;}
  #cvat-sam3-panel.cp-collapsed > *:not(.cp-title){display:none;}
  .cp-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);background:#2a3040;color:#fff;padding:10px 18px;border-radius:8px;opacity:0;transition:all .25s;z-index:2147483647;border:1px solid #4a5878;font-size:13px;}
  .cp-toast.cp-show{opacity:1;transform:translateX(-50%) translateY(0);}
  .cp-toast.cp-err{border-color:#a04855;background:#4a2a30;}
  #cvat-sam3-panel button:disabled{opacity:.45;cursor:not-allowed;}
  `;

  // ------------------------------------------------------------------ 启动
  async function start() {
    buildPanel();
    await onUrlChanged();
    await refreshCredentials();
    // URL 变化监听（SPA）
    var lastPath = location.pathname;
    setInterval(function () {
      if (location.pathname !== lastPath) { lastPath = location.pathname; onUrlChanged(); }
    }, 1000);
    // 帧轮询
    setInterval(pollFrame, 700);
    pollFrame();
    setStatus('就绪', '打开标注作业页后自动加载标签与实例');
  }

  start().catch(function (e) {
    try { toast('面板启动失败: ' + (e.message || e), true); } catch (_) {}
  });
})();
