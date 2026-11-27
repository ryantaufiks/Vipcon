// "Request a demo": every link marked data-demo opens the WhatsApp / email chooser.
// Without this script (or without <dialog> support) the links fall back to the #demo anchor.
(() => {
  const dlg = document.getElementById('demo');
  if (!dlg || typeof dlg.showModal !== 'function') return;
  document.querySelectorAll('[data-demo]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); dlg.showModal(); }));
  dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
  // a click on the dimmed backdrop (outside the panel) closes it; Esc is handled by the browser
  dlg.addEventListener('click', e => {
    if (e.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
  });
  dlg.querySelectorAll('.demo-opt').forEach(a => a.addEventListener('click', () => setTimeout(() => dlg.close(), 150)));
})();
