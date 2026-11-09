-- Removes only the optional RPC interface. Does NOT undo imported data or 001.
-- Review/execute manually; no CASCADE, so unexpected dependencies block safely.
BEGIN;
SET LOCAL lock_timeout = '10s';
DROP FUNCTION public.prerequisite_import_v1(jsonb,jsonb,text,text,text,text);
DROP FUNCTION public.prerequisite_snapshot_v1();
NOTIFY pgrst, 'reload schema';
COMMIT;
