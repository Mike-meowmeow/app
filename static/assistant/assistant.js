// Floating staff help assistant. Questions go to /assistant/ask/; the answer
// and source articles come back as escaped HTML from the server.
(function () {
  const root = document.getElementById('assistant');
  if (!root) return;
  const toggle = root.querySelector('.assistant-toggle');
  const panel = root.querySelector('.assistant-panel');
  const close = root.querySelector('.assistant-close');
  const form = root.querySelector('.assistant-form');
  const input = root.querySelector('#assistant-input');
  const messages = root.querySelector('.assistant-messages');
  const token = form.querySelector('[name=csrfmiddlewaretoken]').value;
  let busy = false;

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) { input.focus(); } else { toggle.focus(); }
  }
  toggle.addEventListener('click', () => setOpen(panel.hidden));
  close.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) setOpen(false); });

  function add(kind, html) {
    const div = document.createElement('div');
    div.className = 'assistant-msg ' + kind;
    if (html !== undefined) div.innerHTML = html;  // server-escaped HTML only
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function sources(list) {
    if (!list.length) return '';
    const items = list.map((s) =>
      '<details><summary>' + s.title + '</summary><div class="mt-1">' + s.html + '</div></details>');
    return '<div class="assistant-sources"><div class="assistant-note">From the help guide:</div>' + items.join('') + '</div>';
  }

  // A confirmation card: nothing runs until Confirm is pressed.
  function cardElement(card) {
    const box = document.createElement('div');
    box.className = 'assistant-card';
    const text = document.createElement('div');
    text.innerHTML = card.summary;  // server-escaped
    const buttons = document.createElement('div');
    buttons.className = 'd-flex gap-2 mt-2';
    const ok = document.createElement('button');
    ok.type = 'button'; ok.className = 'btn btn-sm btn-primary'; ok.textContent = 'Confirm';
    const no = document.createElement('button');
    no.type = 'button'; no.className = 'btn btn-sm btn-outline-secondary'; no.textContent = 'Cancel';
    buttons.append(ok, no);
    box.append(text, buttons);
    no.addEventListener('click', () => { buttons.remove(); box.classList.add('assistant-card-done'); text.insertAdjacentText('beforeend', ' (cancelled)'); });
    ok.addEventListener('click', async () => {
      ok.disabled = true; no.disabled = true;
      ok.textContent = 'Working...';
      try {
        const response = await fetch(root.dataset.runUrl, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
          body: JSON.stringify({ token: card.token }),
        });
        const data = await response.json().catch(() => ({}));
        buttons.remove();
        box.classList.add('assistant-card-done');
        if (response.ok && data.redirect) { window.location.href = data.redirect; return; }
        const result = add('assistant-bot');
        if (response.ok) { result.innerHTML = data.html; } else { result.textContent = data.error || 'That did not work.'; }
      } catch (err) {
        ok.disabled = false; no.disabled = false; ok.textContent = 'Confirm';
        add('assistant-bot').textContent = 'The request could not be sent. Try again.';
      }
    });
    return box;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || busy) return;
    busy = true;
    const user = add('assistant-user');
    user.textContent = question;  // the user's own text, never as HTML
    input.value = '';
    const reply = add('assistant-bot assistant-thinking', 'Looking in the help guide...');
    try {
      const response = await fetch(root.dataset.url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({ question }),
      });
      const data = await response.json().catch(() => ({}));
      reply.classList.remove('assistant-thinking');
      if (!response.ok) {
        reply.textContent = data.error || 'Something went wrong. Please try again.';
      } else {
        reply.innerHTML = data.answer + (data.note ? '<div class="assistant-note">' + data.note + '</div>' : '') + sources(data.sources || []);
        (data.cards || []).forEach((card) => reply.appendChild(cardElement(card)));
      }
    } catch (err) {
      reply.classList.remove('assistant-thinking');
      reply.textContent = 'The assistant could not be reached. Check your connection and try again.';
    }
    messages.scrollTop = messages.scrollHeight;
    busy = false;
    input.focus();
  });
})();
