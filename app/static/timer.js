const sessionElement =
    document.getElementById("running-session");

const timerDisplay =
    document.getElementById("timer-display");

const completeMessage =
    document.getElementById("session-complete-message");


if (sessionElement && timerDisplay) {

    const startedAt =
        new Date(
            sessionElement.dataset.startedAt + "Z"
        );

    const durationSeconds =
        Number(
            sessionElement.dataset.durationSeconds
        );

    const endTime =
        startedAt.getTime() +
        (durationSeconds * 1000);

    let timerInterval = null;


    function updateTimer() {

        const now = Date.now();

        const remainingMilliseconds =
            endTime - now;

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

        timerDisplay.textContent =
            String(minutes).padStart(2, "0") +
            ":" +
            String(seconds).padStart(2, "0");

        if (remainingSeconds <= 0) {

            if (timerInterval !== null) {
                clearInterval(timerInterval);
            }

            if (completeMessage) {
                completeMessage.hidden = false;
            }
        }
    }


    updateTimer();

    timerInterval =
        setInterval(
            updateTimer,
            1000
        );
}