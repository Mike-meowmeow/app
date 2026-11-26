// License / eligibility name boxes (core/credentials.py CredentialNameInput).
//
// The server sends a text box (the real form field) followed by one hidden
// dropdown per kind. This script moves the dropdown above the text box and
// shows it: choosing a listed name fills the text box (kept hidden); choosing
// "Other" shows the text box so any name can be typed. In the qualification
// standard form the criterion picks the kind: licenses for "license",
// eligibilities for "eligibility", and the plain text box otherwise.
// Without JavaScript, the text box with typing suggestions still works.
(function () {
  var OTHER = '__other__';

  function listed(picker, value) {
    return Array.prototype.some.call(picker.options, function (o) {
      return o.value && o.value !== OTHER && o.value === value;
    });
  }

  function setup(input) {
    if (input.dataset.credentialReady) return;
    var base = input.id;
    var pickers = Array.prototype.slice.call(
      document.querySelectorAll('select[data-credential-picker="' + base + '"]'));
    if (!pickers.length) return;
    input.dataset.credentialReady = '1';
    pickers.forEach(function (p) { input.parentNode.insertBefore(p, input); });

    var switchName = input.dataset.credentialSwitch;
    var criterion = switchName && input.form
      ? input.form.querySelector('[name="' + switchName + '"]') : null;

    function currentKind() {
      if (!criterion) return pickers[0].dataset.credentialKind;
      var v = criterion.value;
      return (v === 'license' || v === 'eligibility') ? v : null;
    }

    function visiblePicker() {
      for (var i = 0; i < pickers.length; i++) { if (!pickers[i].hidden) return pickers[i]; }
      return null;
    }

    function show() {
      var kind = currentKind();
      pickers.forEach(function (p) { p.hidden = p.dataset.credentialKind !== kind; });
      var picker = visiblePicker();
      if (!picker) {               // another criterion: the plain text box
        input.hidden = false;
        input.removeAttribute('list');
        return;
      }
      input.setAttribute('list', base + '-' + kind + '-names');
      var value = input.value.trim();
      if (value && listed(picker, value)) {
        picker.value = value;
        input.hidden = true;
      } else if (value) {
        picker.value = OTHER;
        input.hidden = false;
      } else {
        picker.value = '';
        input.hidden = true;
      }
    }

    pickers.forEach(function (picker) {
      picker.addEventListener('change', function () {
        if (picker.value === OTHER) {
          if (listed(picker, input.value.trim())) input.value = '';
          input.hidden = false;
          input.focus();
        } else {
          input.value = picker.value;
          input.hidden = true;
        }
      });
    });

    if (criterion) {
      criterion.addEventListener('change', function () {
        // A name picked from the other kind's list does not carry over.
        var value = input.value.trim();
        var kind = currentKind();
        pickers.forEach(function (p) {
          if (p.dataset.credentialKind !== kind && listed(p, value)) input.value = '';
        });
        show();
      });
    }
    show();
  }

  function init() {
    document.querySelectorAll('input[list$="-names"]').forEach(setup);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
