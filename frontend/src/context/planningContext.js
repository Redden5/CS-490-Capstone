import { createContext, useContext } from 'react';
export const PlanningContext = createContext(null);
export function usePlanning() {
  const value = useContext(PlanningContext);
  if (!value) throw new Error('PlanningProvider is required');
  return value;
}
