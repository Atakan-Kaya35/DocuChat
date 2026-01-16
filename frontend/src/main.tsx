import React from 'react';
import ReactDOM from 'react-dom/client';
import { AuthProvider } from 'react-oidc-context';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { oidcConfig, onSigninCallback } from './auth/oidc-config';

console.log('[App] Starting DocuChat application');
console.log('[App] Current URL:', window.location.href);

// Check if we're on the callback page with auth params
const hasAuthParams = window.location.search.includes('code=') || 
                      window.location.search.includes('error=');
console.log('[App] Has auth callback params:', hasAuthParams);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider 
      {...oidcConfig}
      onSigninCallback={onSigninCallback}
    >
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AuthProvider>
  </React.StrictMode>
);
