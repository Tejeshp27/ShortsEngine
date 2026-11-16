// Poll a private job page while it is active; reload once when the job finishes.
// No provider tokens or private content are stored in this public JavaScript file.
const jobPanel = document.querySelector('[data-job-id]');
if (jobPanel && ['queued', 'running'].includes(jobPanel.dataset.jobStatus)) {
  let polling = true;
  async function refreshJob() {
    // Read progress for the current job; stop if the login session has expired.
    try {
      const response = await fetch(`/jobs/${jobPanel.dataset.jobId}/status`, {cache: 'no-store'});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) {
        polling = false;
        document.querySelector('#job-stage').textContent = 'Refresh this page to check your login and job status.';
        return;
      }
      const job = await response.json();
      document.querySelector('#job-progress').value = job.progress;
      document.querySelector('#job-stage').textContent = job.stage;
      if (!['queued', 'running'].includes(job.status)) {
        polling = false;
        location.reload();
      }
    } catch {
      document.querySelector('#job-stage').textContent = 'Connection interrupted. Checking again shortly…';
    }
    if (polling) setTimeout(refreshJob, 2500);
  }
  setTimeout(refreshJob, 1500);
}
