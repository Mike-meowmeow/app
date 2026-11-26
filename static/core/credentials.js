// Switches the suggestion list of a credential name box (core/credentials.py)
// to match the chosen criterion: licenses for "license", eligibilities for
// "eligibility", none otherwise. Any name can still be typed.
(function () {
  document.querySelectorAll('input[data-credential-switch]').forEach(function (input) {
    var form = input.form;
    if (!form || input.dataset.credentialReady) return;
    input.dataset.credentialReady = '1';
    var select = form.querySelector('[name="' + input.dataset.credentialSwitch + '"]');
    if (!select) return;
    var base = input.dataset.credentialBase;
    function update() {
      var kind = select.value;
      if (kind === 'license' || kind === 'eligibility') {
        input.setAttribute('list', base + '-' + kind + '-names');
      } else {
        input.removeAttribute('list');
      }
    }
    select.addEventListener('change', update);
    update();
  });
})();
