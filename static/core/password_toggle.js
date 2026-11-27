// "Show password" buttons next to password boxes (includes/field.html).
// Without JavaScript the button stays hidden and the box works as usual.
(function () {
  'use strict';

  function setVisible(button, input, visible) {
    var label = visible ? 'Hide password' : 'Show password';
    input.type = visible ? 'text' : 'password';
    // Open eye = show the password; crossed-out eye = hide it again.
    var eye = button.querySelector('.pw-eye');
    var slash = button.querySelector('.pw-eye-slash');
    // SVG has no .hidden property: use Bootstrap's d-none class.
    if (eye) eye.classList.toggle('d-none', visible);
    if (slash) slash.classList.toggle('d-none', !visible);
    button.setAttribute('aria-pressed', visible ? 'true' : 'false');
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
  }

  function init() {
    document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
      var input = document.getElementById(button.getAttribute('data-password-toggle'));
      if (!input || button.dataset.ready) return;
      button.dataset.ready = '1';
      button.hidden = false;
      setVisible(button, input, false);
      button.addEventListener('click', function () {
        setVisible(button, input, input.type === 'password');
        input.focus();
      });
      // Never submit a password while it is shown as plain text, so the
      // browser does not save it in form history.
      if (input.form) {
        input.form.addEventListener('submit', function () { setVisible(button, input, false); });
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  // A restored page (back button) starts hidden again.
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('[data-password-toggle]').forEach(function (button) {
      var input = document.getElementById(button.getAttribute('data-password-toggle'));
      if (input && button.dataset.ready) setVisible(button, input, false);
    });
  });
})();
