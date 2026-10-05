document.addEventListener('DOMContentLoaded', () => {
  const backendUrlInput = document.getElementById('backend-url');
  const btnSave = document.getElementById('btn-save');

  chrome.storage?.local?.get(['backendUrl'], (res) => {
    backendUrlInput.value = res.backendUrl || 'http://localhost:8000';
  });

  btnSave.addEventListener('click', () => {
    const backendUrl = backendUrlInput.value.trim() || 'http://localhost:8000';
    chrome.storage?.local?.set({ backendUrl }, () => {
      btnSave.textContent = 'Đã lưu';
      setTimeout(() => { btnSave.textContent = 'Lưu Cấu Hình'; }, 1500);
    });
  });
});
