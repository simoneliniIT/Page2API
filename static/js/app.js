// Modal handling
function showModal({ title, message, buttons }) {
    const modalOverlay = document.getElementById('modalOverlay');
    const modalTitle = document.getElementById('modalTitle');
    const modalMessage = document.getElementById('modalMessage');
    const modalButtons = document.getElementById('modalButtons');

    modalTitle.textContent = title;
    modalMessage.textContent = message;
    modalButtons.innerHTML = '';

    buttons.forEach(button => {
        const btn = document.createElement('button');
        btn.className = `modal-button ${button.type || 'secondary'}`;
        btn.textContent = button.text;
        btn.onclick = () => {
            hideModal();
            button.onClick?.();
        };
        modalButtons.appendChild(btn);
    });

    modalOverlay.style.display = 'block';
}

function hideModal() {
    document.getElementById('modalOverlay').style.display = 'none';
}

function showAlert(message, title = 'Alert') {
    showModal({
        title,
        message,
        buttons: [{
            text: 'OK',
            type: 'primary',
        }]
    });
}

function showConfirm(message, onConfirm, title = 'Confirm') {
    showModal({
        title,
        message,
        buttons: [
            {
                text: 'Cancel',
                type: 'secondary',
            },
            {
                text: 'OK',
                type: 'primary',
                onClick: onConfirm
            }
        ]
    });
}

// Success message handling
function showSuccessMessage(message, duration = 2000) {
    const messageEl = document.getElementById('successMessage');
    messageEl.textContent = message;
    messageEl.classList.add('show');
    setTimeout(() => {
        messageEl.classList.remove('show');
    }, duration);
}

// Clipboard handling
function copyToClipboard(text) {
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text)
            .then(() => showSuccessMessage('Copied to clipboard!'))
            .catch(err => showAlert('Failed to copy: ' + err.message));
    } else {
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            showSuccessMessage('Copied to clipboard!');
        } catch (err) {
            showAlert('Failed to copy: ' + err.message);
        }
        document.body.removeChild(textarea);
    }
}

// Table handling
function toggleAllCheckboxes(mainCheckbox, selector = '.product-checkbox') {
    const checkboxes = document.querySelectorAll(selector);
    checkboxes.forEach(checkbox => {
        checkbox.checked = mainCheckbox.checked;
    });
    updateButtonStates();
}

function updateButtonStates() {
    const selectedCount = document.querySelectorAll('.product-checkbox:checked').length;
    const buttons = document.querySelectorAll('[data-requires-selection]');
    buttons.forEach(button => {
        button.disabled = selectedCount === 0;
    });
}

function getSelectedIds(selector = '.product-checkbox') {
    const checkboxes = document.querySelectorAll(`${selector}:checked`);
    return Array.from(checkboxes).map(cb => cb.dataset.id);
}

// Content toggle
function toggleContent(id) {
    const preview = document.getElementById(`preview-${id}`);
    const full = document.getElementById(`full-${id}`);
    
    if (preview.classList.contains('hidden')) {
        preview.classList.remove('hidden');
        full.classList.add('hidden');
    } else {
        preview.classList.add('hidden');
        full.classList.remove('hidden');
    }
}

// API calls
async function apiCall(url, method = 'GET', data = null) {
    try {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
            }
        };

        if (data) {
            options.body = JSON.stringify(data);
        }

        const response = await fetch(url, options);
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'API call failed');
        }

        return await response.json();
    } catch (error) {
        console.error('API call error:', error);
        showAlert(error.message, 'Error');
        throw error;
    }
} 