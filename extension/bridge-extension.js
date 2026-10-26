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
