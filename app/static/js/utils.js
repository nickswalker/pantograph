export function formatTimes(times){
    // Convert all time elements to Pacific timezone
    times.forEach(timeEl => {
        // data-datetime lets a non-<time> element (a status badge, say) get the
        // same tooltip treatment without pretending its text is a timestamp.
        const utcTime = new Date(timeEl.getAttribute('datetime') || timeEl.dataset.datetime);

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
        } else if (timeEl.classList.contains('format-as-datetime')) {
            // Update the display text to Pacific time
            timeEl.textContent = utcTime.toLocaleString('en-US', {
                timeZone: 'America/Los_Angeles',
                month: 'short',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit'
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
            // Update the tooltip to show full Pacific time, keeping any lead-in
            // text (e.g. "Approved by Ada Lovelace") ahead of it.
            timeEl.title = (timeEl.dataset.tooltipPrefix || '') + utcTime.toLocaleString('en-US', {
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