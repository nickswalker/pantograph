export function formatTimes(times){
    // Convert all time elements to Pacific timezone
    times.forEach(timeEl => {
        const utcTime = new Date(timeEl.getAttribute('datetime'));

        if (timeEl.classList.contains('format-as-date')) {
            // Update the display text to Pacific time
            timeEl.textContent = utcTime.toLocaleDateString('en-US', {
                timeZone: 'America/Los_Angeles',
                month: 'short',
                day: 'numeric',
                year: 'numeric'
            });
        } else if (timeEl.classList.contains('format-as-time')) {
             // Update the display text to Pacific time
            timeEl.textContent = utcTime.toLocaleTimeString('en-US', {
                timeZone: 'America/Los_Angeles',
                hour: 'numeric',
                minute: '2-digit',
                timeZoneName: 'short'
            });
        } else if (timeEl.classList.contains('format-as-time-with-seconds')) {
             // Update the display text to Pacific time
            timeEl.textContent = utcTime.toLocaleTimeString('en-US', {
                timeZone: 'America/Los_Angeles',
                hour: 'numeric',
                minute: '2-digit',
                second: '2-digit',
                timeZoneName: 'short'
            });
        }

        if (timeEl.classList.contains('format-datetime-tooltip')) {
            // Update the tooltip to show full Pacific time
            timeEl.title = utcTime.toLocaleString('en-US', {
                timeZone: 'America/Los_Angeles',
                month: 'long',
                day: 'numeric',
                year: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
                timeZoneName: 'short'
            });
        }
    });

}