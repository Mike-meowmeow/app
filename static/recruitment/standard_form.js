// Qualification standard form (recruitment/forms.py QualificationStandardForm):
// shows only the fields the chosen criterion uses, and labels them for it
// (years or hours; eligibility or license). Description is always shown. Without JavaScript every
// field shows, and the server ignores values in fields the criterion does not use.
(function () {
  var OPTIONAL = ['degree_level', 'amount', 'name', 'field'];

  function row(form, name) {
    var el = form.querySelector('[name="' + name + '"]');
    return el ? el.closest('.mb-3') : null;
  }

  function setup(select) {
    if (select.dataset.standardReady || !select.form) return;
    select.dataset.standardReady = '1';
    var form = select.form;
    var fields = JSON.parse(select.dataset.standardFields || '{}');
    var fieldLabels = JSON.parse(select.dataset.fieldLabels || '{}');
    var labels = Object.keys(fieldLabels).map(function (name) {
      var el = form.querySelector('[name="' + name + '"]');
      var label = el && el.id ? form.querySelector('label[for="' + el.id + '"]') : null;
      return label ? { label: label, texts: fieldLabels[name], original: label.textContent } : null;
    }).filter(Boolean);

    function show() {
      var used = fields[select.value] || OPTIONAL;
      OPTIONAL.forEach(function (name) {
        var r = row(form, name);
        if (r) r.hidden = used.indexOf(name) === -1;
      });
      labels.forEach(function (item) {
        item.label.textContent = item.texts[select.value] || item.original;
      });
    }

    select.addEventListener('change', show);
    show();
    refreshers.push(show);
  }

  // Browsers may restore the earlier criterion on reload or Back without a
  // change event; check again once the page is fully shown.
  var refreshers = [];
  function refresh() { refreshers.forEach(function (show) { show(); }); }

  function init() {
    document.querySelectorAll('select[data-standard-fields]').forEach(setup);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  window.addEventListener('load', refresh);
  window.addEventListener('pageshow', refresh);
})();
