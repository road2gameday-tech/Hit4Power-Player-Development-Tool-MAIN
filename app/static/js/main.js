(function() {
  // theme
  const themeBtn = document.getElementById('theme-toggle');
  const logo = document.getElementById('site-logo');
  const applyTheme = (t) => {
    document.documentElement.setAttribute('data-theme', t);
    if (logo) {
      logo.src = t === 'light' ? '/static/logos/Hit4PowerMainLogoLight.png' : '/static/logos/Hit4PowerMainLogoDark.png';
    }
  };
  let saved = localStorage.getItem('theme') || 'dark';
  applyTheme(saved);
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      saved = saved === 'light' ? 'dark' : 'light';
      localStorage.setItem('theme', saved);
      applyTheme(saved);
    });
  }
})();
