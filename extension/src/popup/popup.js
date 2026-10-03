document.addEventListener('DOMContentLoaded', () => {
  const backendUrlInput = document.getElementById('backend-url');
  const soundToggle = document.getElementById('sound-toggle');
  const btnSave = document.getElementById('btn-save');
  const statusLabel = document.getElementById('status-label');

  // Load cấu hình hiện tại
  if (chrome.storage?.local) {
    chrome.storage.local.get(['backendUrl', 'soundAlertEnabled'], (res) => {
      if (res.backendUrl) backendUrlInput.value = res.backendUrl;
      if (typeof res.soundAlertEnabled !== 'undefined') soundToggle.checked = res.soundAlertEnabled;
    });
  }

  // Lưu cấu hình
  btnSave.addEventListener('click', () => {
    const backendUrl = backendUrlInput.value.trim() || 'http://localhost:8000';
    const soundAlertEnabled = soundToggle.checked;

    if (chrome.storage?.local) {
      chrome.storage.local.set({ backendUrl, soundAlertEnabled }, () => {
        btnSave.textContent = '✅ Đã lưu!';
        setTimeout(() => { btnSave.textContent = 'Lưu Cấu Hình'; }, 1500);
      });
    } else {
      btnSave.textContent = '✅ Đã lưu!';
    }
  });
});
