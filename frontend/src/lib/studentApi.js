import { PlanningError, request } from './planningApi.js';
import { supabase } from './supabaseClient.js';

export async function studentRequest(path, options) {
  const { data, error } = await supabase.auth.getSession();
  if (error || !data.session?.access_token) {
    throw new PlanningError('Sign in again to access saved student data.', 401);
  }
  options.signal?.throwIfAborted();
  return request(`student/${path}`, { ...options, token: data.session.access_token });
}
