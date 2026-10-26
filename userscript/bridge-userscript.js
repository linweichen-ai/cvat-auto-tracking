/* Tampermonkey 桥接：sessionid 是 HttpOnly，document.cookie 读不到，
 * 用 GM_cookie.list 读取（需要 @grant GM_cookie.list）。
 * 若环境不支持 GM_cookie，返回空，面板会提示用户手动粘贴 sessionid。 */
(function () {
  'use strict';
  function gmList(details) {
    return new Promise(function (resolve) {
      try {
        if (typeof GM_cookie === 'undefined' || !GM_cookie.list) return resolve(null);
        GM_cookie.list(details, function (cookies) {
          try { resolve(cookies && cookies[0] ? cookies[0].value : null); }
          catch (e) { resolve(null); }
        });
      } catch (e) { resolve(null); }
    });
  }
  window.__CVAT_PANEL_BRIDGE__ = {
    getCookiesDoc: function () { return document.cookie || ''; },
    getCredentials: function () {
      return (async function () {
        var sid = await gmList({ domain: '172.16.88.68', name: 'sessionid' });
        if (!sid) sid = await gmList({ url: 'http://172.16.88.68:12080/', name: 'sessionid' });
        var csr = await gmList({ domain: '172.16.88.68', name: 'csrftoken' });
        return { sessionid: sid || '', csrftoken: csr || '' };
      })();
    },
  };
})();
