import { useState, useEffect, useCallback } from 'react';

/**
 * Generic hook for fetching API data with auto-refresh.
 * @param {Function} fetcher - API function to call
 * @param {number} intervalMs - Auto-refresh interval (0 = no refresh)
 * @param {Array} deps - Additional dependencies
 */
export function useApi(fetcher, intervalMs = 0, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const result = await fetcher();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [fetcher, ...deps]);

  useEffect(() => {
    load();
    if (intervalMs > 0) {
      const id = setInterval(load, intervalMs);
      return () => clearInterval(id);
    }
  }, [load, intervalMs]);

  return { data, error, loading, refresh: load };
}

/**
 * Hook for formatted currency values.
 */
export function useCurrency(value, currency = 'AUD') {
  if (value == null) return '--';
  return new Intl.NumberFormat('en-AU', { style: 'currency', currency }).format(value);
}
