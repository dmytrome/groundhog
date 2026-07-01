// Stealth patches, injected into the page's MAIN world at document_start.
// Scope is deliberately small: modern Chrome + the launch flags already clear
// most automation signals (navigator.webdriver reads `false`, real plugins are
// present, window.chrome exists). This only fixes what survives that — verified
// against bot.sannysoft.com. User-Agent and viewport are the caller's job: set
// them over CDP to match the persona/proxy you drive the browser with.

(() => {
  'use strict';

  // navigator.deviceMemory is absent in headless Chrome (a strong bot signal);
  // real Chrome clamps the reported value to 8 GiB. This is what flips
  // sannysoft's CHR_MEMORY check from fail to pass.
  if (navigator.deviceMemory === undefined) {
    try {
      Object.defineProperty(Navigator.prototype, 'deviceMemory', {
        get: () => 8,
        configurable: true,
        enumerable: true,
      });
    } catch {
      /* locked down on this platform — leave the native (missing) value */
    }
  }

  // Headless reports a Notification permission that disagrees with the
  // Permissions API. Align them the way a real browser does.
  if (navigator.permissions && navigator.permissions.query) {
    const query = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (parameters) =>
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission, name: 'notifications', onchange: null })
        : query(parameters);
  }
})();
