const breakElement =
    document.getElementById("break-timer");

const breakDisplay =
    document.getElementById("break-timer-display");


if (breakElement && breakDisplay) {

    const breakUntilSeconds =
        Number(
            breakElement.dataset.breakUntil
        );

    const breakEndTime =
        breakUntilSeconds * 1000;


    function updateBreakTimer() {

        const now = Date.now();

        const remainingMilliseconds =
            breakEndTime - now;

        const remainingSeconds =
            Math.max(
                0,
                Math.ceil(
                    remainingMilliseconds / 1000
                )
            );

        const minutes =
            Math.floor(
                remainingSeconds / 60
            );

        const seconds =
            remainingSeconds % 60;

        breakDisplay.textContent =
            String(minutes).padStart(2, "0") +
            ":" +
            String(seconds).padStart(2, "0");

        if (remainingSeconds <= 0) {

            clearInterval(breakTimerInterval);

            window.location.href = "/timer/";
        }
    }


    updateBreakTimer();

    const breakTimerInterval =
        setInterval(
            updateBreakTimer,
            1000
        );
}