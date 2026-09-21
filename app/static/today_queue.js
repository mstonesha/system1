(function () {
    const HANDLE_SELECTOR = ".queue-drag-handle";
    const ITEM_SELECTOR = ".today-queue-item";
    const QUEUE_SELECTOR = "ol.today-queue";

    let draggedItem = null;
    let sourceQueue = null;
    let originalItems = [];
    let dropCompleted = false;
    let submitInFlight = false;

    function requestHeaders() {
        const headers = {
            "HX-Request": "true",
        };
        const raw = document.body.getAttribute("hx-headers");

        if (!raw) {
            return headers;
        }

        try {
            const extra = JSON.parse(raw);
            Object.keys(extra).forEach(function (key) {
                headers[key] = extra[key];
            });
        } catch (error) {
            return headers;
        }

        return headers;
    }

    function itemIds(items) {
        return items.map(function (item) {
            return item.getAttribute("data-daily-task-id");
        });
    }

    function currentItems(queue) {
        return Array.from(
            queue.querySelectorAll(":scope > " + ITEM_SELECTOR)
        );
    }

    function restoreItems(queue, items) {
        if (!queue || !items.length) {
            return;
        }

        items.forEach(function (item) {
            queue.appendChild(item);
        });
    }

    function clearDragClasses(item, queue) {
        if (item) {
            item.classList.remove("is-dragging");
        }

        if (queue) {
            queue.classList.remove("is-reordering");
        }
    }

    function getDragAfterElement(queue, y) {
        const items = Array.from(
            queue.querySelectorAll(
                ITEM_SELECTOR + ":not(.is-dragging)"
            )
        );

        return items.reduce(
            function (closest, child) {
                const box = child.getBoundingClientRect();
                const offset = y - box.top - box.height / 2;

                if (offset < 0 && offset > closest.offset) {
                    return {
                        offset: offset,
                        element: child,
                    };
                }

                return closest;
            },
            {
                offset: Number.NEGATIVE_INFINITY,
                element: null,
            }
        ).element;
    }

    function swapQueue(queue, html) {
        if (window.htmx && typeof window.htmx.swap === "function") {
            window.htmx.swap(queue, html, {
                swapStyle: "outerHTML",
            });
            return;
        }

        const parent = queue.parentNode;
        queue.outerHTML = html;

        if (window.htmx && parent) {
            window.htmx.process(parent);
        }
    }

    function submitReorder(queue, snapshot) {
        const originalIds = itemIds(snapshot);
        const items = currentItems(queue);
        const ids = itemIds(items);

        if (
            ids.length === originalIds.length
            && ids.every(function (id, index) {
                return id === originalIds[index];
            })
        ) {
            return;
        }

        const targetDate = queue.getAttribute("data-target-date");

        if (!targetDate || ids.some(function (id) {
            return !id;
        })) {
            restoreItems(queue, snapshot);
            return;
        }

        const formData = new FormData();
        formData.append("target_date", targetDate);
        ids.forEach(function (id) {
            formData.append("daily_task_id", id);
        });

        submitInFlight = true;

        fetch("/today/reorder", {
            method: "POST",
            body: formData,
            headers: requestHeaders(),
            credentials: "same-origin",
        }).then(function (response) {
            if (!response.ok) {
                restoreItems(queue, snapshot);
                return null;
            }

            return response.text();
        }).then(function (html) {
            if (!html) {
                return;
            }

            swapQueue(queue, html);
        }).catch(function () {
            restoreItems(queue, snapshot);
        }).finally(function () {
            submitInFlight = false;
        });
    }

    document.addEventListener("dragstart", function (event) {
        const handle = event.target.closest(HANDLE_SELECTOR);

        if (!handle) {
            return;
        }

        const item = handle.closest(ITEM_SELECTOR);
        const queue = handle.closest(QUEUE_SELECTOR);

        if (!item || !queue) {
            event.preventDefault();
            return;
        }

        draggedItem = item;
        sourceQueue = queue;
        originalItems = currentItems(queue);
        dropCompleted = false;
        submitInFlight = false;

        item.classList.add("is-dragging");
        queue.classList.add("is-reordering");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(
            "text/plain",
            item.getAttribute("data-daily-task-id") || ""
        );
    });

    document.addEventListener("dragover", function (event) {
        if (!draggedItem || !sourceQueue) {
            return;
        }

        const queue = event.target.closest(QUEUE_SELECTOR);

        if (!queue || queue !== sourceQueue) {
            return;
        }

        event.preventDefault();
        event.dataTransfer.dropEffect = "move";

        const after = getDragAfterElement(queue, event.clientY);

        if (after == null) {
            queue.appendChild(draggedItem);
            return;
        }

        if (after !== draggedItem) {
            queue.insertBefore(draggedItem, after);
        }
    });

    document.addEventListener("drop", function (event) {
        if (!draggedItem || !sourceQueue) {
            return;
        }

        const queue = event.target.closest(QUEUE_SELECTOR);

        if (!queue || queue !== sourceQueue) {
            return;
        }

        event.preventDefault();
        dropCompleted = true;

        const snapshot = originalItems.slice();
        clearDragClasses(draggedItem, queue);
        submitReorder(queue, snapshot);
    });

    document.addEventListener("dragend", function () {
        if (!draggedItem) {
            return;
        }

        const item = draggedItem;
        const queue = sourceQueue;
        const snapshot = originalItems.slice();

        clearDragClasses(item, queue);

        if (!dropCompleted && !submitInFlight) {
            restoreItems(queue, snapshot);
        }

        draggedItem = null;
        sourceQueue = null;
        originalItems = [];
    });
})();
