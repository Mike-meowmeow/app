// New-vacancy form: pre-fill the description from the chosen catalog position
// and preview the default requirements that the server will copy. An edited
// description is never replaced without confirmation. Without JavaScript the
// server still applies the position's defaults (a blank description uses the
// position's default).
(function () {
  'use strict';
  var dataEl = document.getElementById('position-defaults');
  var select = document.querySelector('[data-position-select]');
  var description = document.querySelector('[data-position-description]');
  var preview = document.getElementById('position-preview');
  var body = document.getElementById('position-preview-body');
  var reload = document.getElementById('position-reload');
  if (!dataEl || !select || !description || !preview) return;

  var defaults = JSON.parse(dataEl.textContent);
  var lastFilled = null;

  function current() { return defaults[select.value] || null; }

  function edited() {
    var text = description.value.trim();
    return text !== '' && text !== (lastFilled || '').trim();
  }

  function fill(pos) {
    description.value = pos.description;
    lastFilled = pos.description;
  }

  function section(title, items) {
    var wrap = document.createElement('div');
    wrap.className = 'mb-1';
    var label = document.createElement('span');
    label.className = 'text-muted';
    label.textContent = title + ': ';
    wrap.appendChild(label);
    wrap.appendChild(document.createTextNode(items.length ? items.join('; ') : 'none'));
    return wrap;
  }

  function render(pos) {
    body.textContent = '';
    if (!pos) { preview.classList.add('d-none'); return; }
    body.appendChild(section('Documents', pos.documents.map(function (d) {
      return d.name + (d.is_required ? ' (required)' : ' (optional)');
    })));
    body.appendChild(section('Qualification standards', pos.standards.map(function (s) {
      return s.description;
    })));
    body.appendChild(section('Subjects and competencies', pos.competencies.map(function (c) {
      return c.name;
    })));
    preview.classList.remove('d-none');
  }

  function apply(askFirst) {
    var pos = current();
    render(pos);
    if (!pos) return;
    if (edited() && askFirst &&
        !window.confirm('Replace your edited description with the default description of "' +
                        pos.title + '"?')) {
      return;
    }
    fill(pos);
  }

  select.addEventListener('change', function () { apply(true); });
  if (reload) reload.addEventListener('click', function () { apply(true); });

  // Re-displayed form (e.g. after a validation error): keep what HR typed.
  var pos = current();
  render(pos);
  if (pos) {
    if (description.value.trim() === '') fill(pos);
    else if (description.value.trim() === pos.description.trim()) lastFilled = pos.description;
  }
})();
