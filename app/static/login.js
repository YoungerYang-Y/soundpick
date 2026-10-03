const password = document.getElementById('password');
const toggle = document.getElementById('password-toggle');

if (password && toggle) {
  toggle.hidden = false;
  // Keep the input's focus and selection when tapping the icon on a phone.
  toggle.addEventListener('pointerdown', event => event.preventDefault());
  toggle.addEventListener('click', () => {
    const { selectionStart, selectionEnd, selectionDirection } = password;
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    // Flush the type change before restoring the input selection.
    password.getBoundingClientRect();
    password.setSelectionRange(selectionStart, selectionEnd, selectionDirection);
    toggle.setAttribute('aria-pressed', String(visible));
    toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
  });
}
