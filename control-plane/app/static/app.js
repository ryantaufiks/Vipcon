document.addEventListener('submit', (e) => {
  const msg = e.target.getAttribute('data-confirm')
  if (msg && !window.confirm(msg)) e.preventDefault()
})
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-print]')) window.print()
  if (e.target.closest('[data-back]')) { e.preventDefault(); history.back() }
})
