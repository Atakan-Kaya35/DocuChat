import { useAuth } from 'react-oidc-context';

/**
 * Custom hook for authentication operations
 */
export function useAuthentication() {
  const auth = useAuth();

  const login = () => {
    auth.signinRedirect();
  };

  const logout = () => {
    auth.signoutRedirect();
  };

  const getAccessToken = (): string | null => {
    return auth.user?.access_token ?? null;
  };

  return {
    isAuthenticated: auth.isAuthenticated,
    isLoading: auth.isLoading,
    user: auth.user,
    login,
    logout,
    getAccessToken,
    error: auth.error,
  };
}
