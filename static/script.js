const startForm = document.getElementById('start-form');
const stopBtn = document.getElementById('stop-btn');
const pauseBtn = document.getElementById('pause-btn');

const threadsContainer = document.getElementById('threads-container');
const overallProgressBar = document.getElementById('overall-progress');
const overallProgressText = document.getElementById('overall-progress-text');

let isPaused = false;

startForm.addEventListener('submit', function(e) {
  e.preventDefault();

  const formData = new FormData(e.target);
  const data = {};
  formData.forEach((value, key) => { data[key] = value });

  fetch('/', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams(data)
  })
  .then(res => res.json())
  .then(response => {
    if(response.error){
      alert("Ошибка: " + response.error);
    } else {
      stopBtn.disabled = false;
      pauseBtn.disabled = false;
      isPaused = false;
      pauseBtn.textContent = "Pause";
    }
  });
});

stopBtn.addEventListener('click', function() {
  fetch('/stop', {method: 'POST'})
  .then(() => {
    stopBtn.disabled = true;
    pauseBtn.disabled = true;
    isPaused = false;
    pauseBtn.textContent = "Pause";
  });
});

pauseBtn.addEventListener('click', function() {
  if (!isPaused) {
    fetch('/pause', {method: 'POST'})
      .then(() => {
        isPaused = true;
        pauseBtn.textContent = "Resume";
      });
  } else {
    fetch('/resume', {method: 'POST'})
      .then(() => {
        isPaused = false;
        pauseBtn.textContent = "Pause";
      });
  }
});

function updateThreadsStatus(threads) {
  threadsContainer.innerHTML = ''; // очистить

  threads.forEach(thread => {
    const threadDiv = document.createElement('div');
    threadDiv.classList.add('thread-status');

    const indicator = document.createElement('div');
    indicator.classList.add('thread-indicator');

    if(thread.status === 'active') indicator.classList.add('active');
    else if(thread.status === 'paused') indicator.classList.add('paused');
    else if(thread.status === 'stopped') indicator.classList.add('stopped');

    const label = document.createElement('span');
    label.textContent = `Поток ${thread.id}: ${thread.status}`;

    const progress = document.createElement('progress');
    progress.classList.add('thread-progress');
    progress.max = 100;
    progress.value = thread.progress;

    threadDiv.appendChild(indicator);
    threadDiv.appendChild(label);
    threadDiv.appendChild(progress);

    threadsContainer.appendChild(threadDiv);
  });
}

setInterval(() => {
  fetch('/progress')
    .then(res => res.json())
    .then(data => {
      document.getElementById('log').textContent = data.result || '';

      if (data.threads) {
        updateThreadsStatus(data.threads);
      }

      if (data.overall_progress !== undefined) {
        overallProgressBar.value = data.overall_progress;
        overallProgressText.textContent = data.overall_progress + '%';
      }

      if (data.finished) {
        stopBtn.disabled = true;
        pauseBtn.disabled = true;
        pauseBtn.textContent = "Pause";
        isPaused = false;
      }
    });
}, 2000);
