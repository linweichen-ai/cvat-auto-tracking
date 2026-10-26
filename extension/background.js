/* MV3 background：提供 HttpOnly cookie 读取服务 */
'use strict';
const CVAT_ORIGIN = 'http://172.16.88.68:12080/';

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'CP_GET_COOKIES') {
    (async () => {
      try {
        const s = await chrome.cookies.get({ url: CVAT_ORIGIN, name: 'sessionid' });
        const c = await chrome.cookies.get({ url: CVAT_ORIGIN, name: 'csrftoken' });
        sendResponse({ sessionid: s ? s.value : '', csrftoken: c ? c.value : '' });
      } catch (e) {
        sendResponse({ sessionid: '', csrftoken: '', error: String(e) });
      }
    })();
    return true; // 异步响应
  }
});
