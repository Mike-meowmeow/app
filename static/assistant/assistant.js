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
