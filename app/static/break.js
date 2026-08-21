const breakElement =
    document.getElementById("break-timer");

const breakDisplay =
    document.getElementById("break-timer-display");

const breakProgress =
    document.getElementById("break-progress");


if (breakElement && breakDisplay) {

    const breakUntilSeconds =
        Number(
            breakElement.dataset.breakUntil
        );

    const durationSeconds =
        Number(
            breakElement.dataset.breakDurationSeconds
        );

    const breakEndTime =
        breakUntilSeconds * 1000;

    const breakStartTime =
        breakEndTime
        - (durationSeconds * 1000);


    function updateBreakTimer() {

        const now =
            Date.now();

        const remainingSeconds =
            Math.max(
                0,
                Math.ceil(
                    (
                        breakEndTime
                        - now
                    ) / 1000
                )
            );

        const minutes =
            Math.floor(
                remainingSeconds / 60
            );

        const seconds =
            remainingSeconds % 60;

        breakDisplay.textContent =
            String(minutes).padStart(2, "0")
            + ":"
            + String(seconds).padStart(2, "0");


        if (breakProgress) {

            const elapsedMilliseconds =
                Math.max(
                    0,
                    now - breakStartTime
                );

            const progress =
                Math.min(
                    1,
                    elapsedMilliseconds
                    / (durationSeconds * 1000)
                );

            breakProgress.style.width =
                `${progress * 100}%`;
        }


        if (remainingSeconds <= 0) {

            clearInterval(breakTimerInterval);

            window.location.href =
                "/timer/";
        }
    }


    updateBreakTimer();

    const breakTimerInterval =
        setInterval(
            updateBreakTimer,
            1000
        );
}