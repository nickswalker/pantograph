/**
 * Shared API Client
 * Consolidates common JavaScript patterns across all templates
 */

// URL configuration - to be set by templates using url_for
let urlConfig = {};

// Function to configure URLs from templates
export function configureUrls(config) {
    urlConfig = { ...urlConfig, ...config };
}

// Base API Client with common functionality
export class ApiClient {
    constructor() {
        this.defaultTimeout = 30000; // 30 seconds
    }

    // Get configured URL
    getUrl(key) {
        return urlConfig[key];
    }

    // Replace URL placeholders with actual values
    buildUrl(key, params = {}) {
        let url = this.getUrl(key);

        // Replace placeholders like __SHORT_ID__ with actual values
        Object.keys(params).forEach(param => {
            const placeholder = `__${param.toUpperCase()}__`;
            url = url.replace(placeholder, params[param]);
        });

        return url;
    }

    // Generic fetch wrapper with loading state management
    async request(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        };

        try {
            const response = await fetch(url, defaultOptions);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }

            return { success: true, data };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Form submission with loading state
    async submitForm(form, submitButton = null, loadingText = 'Processing...') {
        const formData = new FormData(form);
        const url = form.action || form.getAttribute('action') || window.location.pathname;

        // Show loading state
        if (submitButton) {
            this.setButtonLoading(submitButton, loadingText);
        }

        try {
            const response = await fetch(url, {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Request failed');
            }

            return { success: true, data: result };
        } catch (error) {
            return { success: false, error: error.message };
        } finally {
            // Reset button state
            if (submitButton) {
                this.resetButton(submitButton);
            }
        }
    }

    // Button state management
    setButtonLoading(button, text = 'Loading...') {
        if (!button.dataset.originalContent) {
            button.dataset.originalContent = button.innerHTML;
        }
        button.disabled = true;
        button.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${text}`;
    }

    resetButton(button) {
        if (button.dataset.originalContent) {
            button.innerHTML = button.dataset.originalContent;
            delete button.dataset.originalContent;
        }
        button.disabled = false;
    }

    // Alert management
    showAlert(container, type, message, icon = null) {
        if (typeof container === 'string') {
            container = document.getElementById(container);
        }

        if (!container) return;

        const iconHtml = icon ? `<ion-icon name="${icon}" class="me-2"></ion-icon>` : '';
        container.innerHTML = `
            <div class="alert alert-${type}" role="alert">
                ${iconHtml}${message}
            </div>
        `;
    }

    clearAlerts(container) {
        if (typeof container === 'string') {
            container = document.getElementById(container);
        }
        if (container) {
            container.innerHTML = '';
        }
    }

    // Confirmation dialogs
    confirm(message, title = 'Are you sure?') {
        return window.confirm(`${title}\n\n${message}`);
    }

    // Page-level banner, pinned to the top of the viewport. Stays until dismissed
    // or replaced, since toasts disappearing before they can be read is the problem
    // this replaces.
    showBanner(message, type = 'info') {
        const banner = document.getElementById('sticky-banner-container');
        if (!banner) return;

        banner.innerHTML = `
            <div class="alert alert-${type} alert-dismissible mb-0" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
    }

    // Feedback scoped to a modal that's still open (e.g. a failed confirm action).
    // Injects into the modal's body so the user sees it without the modal closing.
    showModalAlert(modal, type, message) {
        if (typeof modal === 'string') {
            modal = document.getElementById(modal);
        }
        if (!modal) return;
        const body = modal.querySelector('.modal-body');
        if (!body) return;

        let alertEl = body.querySelector('.js-modal-alert');
        if (!alertEl) {
            alertEl = document.createElement('div');
            alertEl.className = 'js-modal-alert';
            body.prepend(alertEl);
        }
        alertEl.innerHTML = `
            <div class="alert alert-${type} alert-dismissible" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
    }

    clearModalAlert(modal) {
        if (typeof modal === 'string') {
            modal = document.getElementById(modal);
        }
        const alertEl = modal && modal.querySelector('.js-modal-alert');
        if (alertEl) alertEl.innerHTML = '';
    }

    // Feedback scoped to a specific table row (e.g. a failed per-member action).
    // Inserts a full-width alert row directly below the row that triggered it.
    showRowAlert(row, type, message) {
        if (!row) return;
        const existing = row.nextElementSibling;
        if (existing && existing.classList.contains('js-row-alert')) {
            existing.remove();
        }

        const alertRow = document.createElement('tr');
        alertRow.className = 'js-row-alert';
        alertRow.innerHTML = `
            <td colspan="${row.children.length}" class="p-0">
                <div class="alert alert-${type} alert-dismissible m-2 mb-2" role="alert">
                    ${message}
                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                </div>
            </td>
        `;
        row.after(alertRow);
    }

    // Redirect with delay
    redirectAfterDelay(url, delay = 2000) {
        setTimeout(() => {
            window.location.href = url;
        }, delay);
    }
}

// Team Management API
export class TeamAPI extends ApiClient {
    // Create team
    async createTeam(formData) {
        const url = this.getUrl('createTeam');
        return await this.request(url, {
            method: 'POST',
            body: formData,
            headers: {} // Let browser set content-type for FormData
        });
    }

    // Withdraw team
    async withdrawTeam(teamId) {
        const url = this.buildUrl('withdrawTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Un-withdraw team
    async unwithdrawTeam(teamId) {
        const url = this.buildUrl('unwithdrawTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Delete team
    async deleteTeam(teamId) {
        const url = this.buildUrl('deleteTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'DELETE'
        });
    }

    // Update team details
    async updateTeamDetails(teamId, data) {
        const url = this.buildUrl('updateTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    // Cancel team (admin)
    async cancelTeam(teamId) {
        const url = this.buildUrl('cancelTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Approve team (admin)
    async approveTeam(teamId) {
        const url = this.buildUrl('approveTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Close team
    async closeTeam(teamId) {
        const url = this.buildUrl('closeTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Reopen team
    async reopenTeam(teamId) {
        const url = this.buildUrl('reopenTeam', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Generate invite link
    async generateInviteLink(teamId) {
        const url = this.buildUrl('generateInviteLink', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Manage team password (set or remove)
    async manageTeamPassword(teamId, password) {
        const url = this.buildUrl('manageTeamPassword', { team_id: teamId });
        return await this.request(url, {
            method: 'POST',
            body: JSON.stringify({ password: password })
        });
    }

    // Update baton serial (admin)
    async updateBatonSerial(teamId, newSerial) {
        const url = this.buildUrl('updateBatonSerial', { team_id: teamId });
        return await this.request(url, {
            method: 'POST',
            body: JSON.stringify({ baton_serial: newSerial })
        });
    }

    // Send payment reminder (admin)
    async sendPaymentReminder(teamId) {
        const url = this.buildUrl('sendPaymentReminder', { team_id: teamId });
        return await this.request(url, {
            method: 'POST'
        });
    }
}

// Membership Management API
export class MembershipAPI extends ApiClient {
    // Join team
    async joinTeam(formData) {
        const url = this.getUrl('joinTeam');
        return await this.request(url, {
            method: 'POST',
            body: formData,
            headers: {} // Let browser set content-type for FormData
        });
    }

    // Update membership
    async updateMembership(formData) {
        const url = this.getUrl('myRegistration');
        return await this.request(url, {
            method: 'POST',
            body: formData,
            headers: {} // Let browser set content-type for FormData
        });
    }

    // Withdraw membership
    async withdrawMembership(teamId, membershipId) {
        const url = this.buildUrl('withdrawMembership', {
            team_id: teamId,
            user_id: membershipId
        });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Un-withdraw membership
    async unwithdrawMembership(teamId, membershipId) {
        const url = this.buildUrl('unwithdrawMembership', {
            team_id: teamId,
            user_id: membershipId
        });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Remove member
    async removeMember(teamId, userId) {
        const url = this.buildUrl('removeMember', {
            team_id: teamId,
            user_id: userId
        });
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Promote to captain
    async promoteToCaptain(teamId, newCaptainId) {
        const url = this.buildUrl('promoteToCaptain', {
            team_id: teamId,
            user_id: newCaptainId
        });
        return await this.request(url, {
            method: 'POST'
        });
    }
}

// User Management API
export class UserAPI extends ApiClient {
    // Delete user account
    async deleteUser(user_id) {
        const url = this.buildUrl('deleteUser', { user_id: user_id });
        return await this.request(url, {
            method: 'DELETE'
        });
    }

    // Update user preferences
    async updatePreferences(data) {
        const url = this.getUrl('updatePreferences');
        return await this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    }

    // Update notification preferences
    async updateNotificationPreferences(data) {
        const url = this.getUrl('updateNotificationPreferences');
        return await this.request(url, {
            method: 'PATCH',
            body: JSON.stringify(data)
        });
    }
}

// Admin Management API
export class AdminAPI extends ApiClient {
    // Send registration reminder (admin)
    async sendRegistrationReminder() {
        const url = this.getUrl('sendRegistrationReminder');
        return await this.request(url, {
            method: 'POST'
        });
    }

    // Delete all images (admin)
    async deleteAllImages() {
        const url = this.getUrl('deleteAllImages');
        return await this.request(url, {
            method: 'POST'
        });
    }
}

// Image Management API
export class ImageAPI extends ApiClient {
    // Upload images to team
    async uploadImages(teamId, formData, progressCallback = null) {
        try {
            const xhr = new XMLHttpRequest();
            const url = this.buildUrl('uploadImages', { team_id: teamId });

            return new Promise((resolve, reject) => {
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable && progressCallback) {
                        const percentComplete = (e.loaded / e.total) * 100;
                        progressCallback(percentComplete);
                    }
                });

                xhr.addEventListener('load', () => {
                    try {
                        const result = JSON.parse(xhr.responseText);
                        if (xhr.status >= 200 && xhr.status < 300) {
                            resolve({ success: true, data: result });
                        } else {
                            resolve({ success: false, error: result.error || 'Upload failed' });
                        }
                    } catch (error) {
                        resolve({ success: false, error: 'Invalid response' });
                    }
                });

                xhr.addEventListener('error', () => {
                    resolve({ success: false, error: 'Network error' });
                });

                xhr.open('POST', url);
                xhr.send(formData);
            });
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    // Delete image by ID
    async deleteImage(teamId, imageId) {
        const url = this.buildUrl('deleteImage', { team_id: teamId, image_id: imageId });
        return await this.request(url, {
            method: 'DELETE'
        });
    }

    // Get image metadata
    async getImageMetadata(file) {
        return new Promise((resolve) => {
            // This would integrate with ExifReader for metadata extraction
            // Implementation depends on the specific needs
            resolve({ success: true, data: {} });
        });
    }
}

// Utility functions for common operations
export class Utils {
    // Copy to clipboard
    static async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = text;
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
                return true;
            } catch (fallbackErr) {
                return false;
            } finally {
                document.body.removeChild(textArea);
            }
        }
    }

    // Format time input (MM:SS or HH:MM)
    static formatTimeInput(input, maxMinutes = 99) {
        input.addEventListener('input', function(e) {
            let value = e.target.value.replace(/[^\d]/g, '');
            if (value.length >= 3) {
                const minutes = value.substring(0, value.length - 2);
                const seconds = value.substring(value.length - 2);
                if (parseInt(minutes) <= maxMinutes) {
                    value = minutes + ':' + seconds;
                }
            }
            e.target.value = value;
        });
    }

    // Password field toggle
    static setupPasswordToggle(passwordField, toggleButton) {
        toggleButton.addEventListener('change', function() {
            passwordField.style.display = this.checked ? 'block' : 'none';
            passwordField.required = this.checked;
            if (!this.checked) {
                passwordField.value = '';
            }
        });
    }

    // Form validation helpers
    static validateEmail(email) {
        const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return re.test(email);
    }

    static validateTime(time) {
        const re = /^\d{1,2}:\d{2}$/;
        return re.test(time);
    }

    // Debounce function for search inputs
    static debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
}

// Global instances for easy access
export const api = new ApiClient();
export const teamAPI = new TeamAPI();
export const membershipAPI = new MembershipAPI();
export const userAPI = new UserAPI();
export const adminAPI = new AdminAPI();
export const imageAPI = new ImageAPI();

// Make utilities available globally
window.Utils = Utils;

// Export everything for module usage
export default {
    ApiClient,
    TeamAPI,
    MembershipAPI,
    UserAPI,
    ImageAPI,
    Utils,
    api,
    teamAPI,
    membershipAPI,
    userAPI,
    imageAPI
};
