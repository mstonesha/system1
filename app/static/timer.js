const sessionElement =
    document.getElementById("running-session");

const timerDisplay =
    document.getElementById("timer-display");

const progressFill =
    document.getElementById("focus-progress");

const completeMessage =
    document.getElementById("session-complete-message");

const runningControls =
    document.getElementById("running-controls");

const actualDuration =
    document.getElementById("actual-duration");

const noteField =
    document.getElementById("session-note");

const noteCounter =
    document.getElementById("note-counter");


if (sessionElement && timerDisplay) {

    const startedAt =
        new Date(
            sessionElement.dataset.startedAt
        );

    const durationSeconds =
        Number(
            sessionElement.dataset.durationSeconds
        );

    const plannedEndTime =
        startedAt.getTime()
        + (durationSeconds * 1000);

    const endedAtValue =
        sessionElement.dataset.endedAt;

    const recordedEndTime =
        endedAtValue
            ? new Date(endedAtValue).getTime()
            : null;

    let timerInterval = null;


    function formatClock(seconds) {

        const minutes =
            Math.floor(seconds / 60);

        const remainder =
            seconds % 60;

        return (
            String(minutes).padStart(2, "0")
            + ":"
            + String(remainder).padStart(2, "0")
        );
    }


    function showOutcome(endTime) {

        if (timerInterval !== null) {
            clearInterval(timerInterval);
        }

        if (runningControls) {
            runningControls.hidden = true;
        }

        if (completeMessage) {
            completeMessage.hidden = false;
        }

        const elapsedSeconds =
            Math.max(
                0,
                Math.min(
                    durationSeconds,
                    Math.floor(
                        (
                            endTime
                            - startedAt.getTime()
                        ) / 1000
                    )
                )
            );

        if (actualDuration) {

            const minutes =
                Math.floor(elapsedSeconds / 60);

            const seconds =
                elapsedSeconds % 60;

            actualDuration.textContent =
                seconds === 0
                    ? `${minutes} min`
                    : `${minutes}m ${seconds}s`;
        }

        if (progressFill) {

            const progress =
                Math.min(
                    1,
                    elapsedSeconds / durationSeconds
                );

            progressFill.style.width =
                `${progress * 100}%`;
        }
    }


    function updateTimer() {

        if (recordedEndTime !== null) {

            const remainingSeconds =
                Math.max(
                    0,
                    Math.ceil(
                        (
                            plannedEndTime
                            - recordedEndTime
                        ) / 1000
                    )
                );

            timerDisplay.textContent =
                formatClock(remainingSeconds);

            showOutcome(recordedEndTime);

            return;
        }

        const now =
            Date.now();

        const remainingSeconds =
            Math.max(
                0,
                Math.ceil(
                    (
                        plannedEndTime
                        - now
                    ) / 1000
                )
            );

        const elapsedSeconds =
            Math.max(
                0,
                durationSeconds
                - remainingSeconds
            );

        timerDisplay.textContent =
            formatClock(remainingSeconds);

        if (progressFill) {

            const progress =
                Math.min(
                    1,
                    elapsedSeconds / durationSeconds
                );

            progressFill.style.width =
                `${progress * 100}%`;
        }

        if (remainingSeconds <= 0) {

            timerDisplay.textContent =
                "00:00";

            showOutcome(plannedEndTime);
        }
    }


    updateTimer();

    if (recordedEndTime === null) {

        timerInterval =
            setInterval(
                updateTimer,
                1000
            );
    }
}


if (noteField && noteCounter) {

    function updateNoteCounter() {

        noteCounter.textContent =
            `${noteField.value.length} / 64`;
    }

    noteField.addEventListener(
        "input",
        updateNoteCounter
    );

    updateNoteCounter();
}