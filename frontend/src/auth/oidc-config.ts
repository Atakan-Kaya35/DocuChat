import { WebStorageStateStore, UserManagerSettings, Log } from 'oidc-client-ts';

// Enable OIDC debug logging in development
if (import.meta.env.DEV || import.meta.env.VITE_DEBUG_OIDC === 'true') {
  Log.setLogger(console);
  Log.setLevel(Log.DEBUG);
  console.log('[OIDC] Debug logging enabled');
}

/**
 * OIDC Configuration for Keycloak
 * 
 * Token Storage Strategy (MVP):
 * - Using sessionStorage via WebStorageStateStore
 * - Tokens persist across page refreshes within the same session
 * - Tokens are cleared when browser tab is closed
 * - This is acceptable for MVP; production should consider:
 *   - In-memory only (most secure but loses state on refresh)
 *   - HttpOnly cookies (requires backend support)
 */

const keycloakUrl = import.meta.env.VITE_KEYCLOAK_URL || 'http://localhost';
const realm = import.meta.env.VITE_KEYCLOAK_REALM || 'docuchat';
const clientId = import.meta.env.VITE_KEYCLOAK_CLIENT_ID || 'docuchat-frontend';

// Construct the issuer URL (Keycloak realm endpoint)
// NOTE: Keycloak's OIDC endpoints are at /realms/{realm}, not /auth/realms/{realm}
// The /auth prefix is only used when KC_HTTP_RELATIVE_PATH is set
const authority = `${keycloakUrl}/realms/${realm}`;

// Log configuration for debugging
console.log('[OIDC] Configuration:', {
  authority,
  clientId,
  redirectUri: `${window.location.origin}/callback`,
  currentUrl: window.location.href,
});

export const oidcConfig: UserManagerSettings = {
  authority,
  client_id: clientId,
  redirect_uri: `${window.location.origin}/callback`,
  post_logout_redirect_uri: window.location.origin,
  
  // Use Authorization Code flow with PKCE (recommended for SPAs)
  response_type: 'code',
  
  // Scopes to request
  scope: 'openid profile email',
  
  // Token storage: sessionStorage for MVP
  // This persists across refreshes but clears on tab close
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  
  // Automatically renew tokens before expiry
  automaticSilentRenew: true,
  
  // Load user info from userinfo endpoint
  loadUserInfo: true,
  
  // Handle callback in popup or redirect (using redirect)
  // popup: false is default
  
  // Metadata can be discovered automatically, but we specify for clarity
  // Keycloak's well-known endpoint: {authority}/.well-known/openid-configuration
  
  // Monitor events for debugging
  monitorSession: false, // Disable session monitoring to avoid iframe issues
};

/**
 * Callback handler for when signin redirect completes
 * This removes the code/state from URL to clean up the address bar
 */
export const onSigninCallback = (): void => {
  console.log('[OIDC] Signin callback triggered, cleaning up URL');
  // Remove the code and state from the URL
  window.history.replaceState({}, document.title, window.location.pathname);
};
