import { useState } from "react";
import marshallLogo from "../assets/marshall-header-logo.webp";
import { supabase } from "../lib/supabaseClient.js";
import "../styles/Login.css";

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleLogin(event) {
    event.preventDefault();
    if (loading) return;
    setLoading(true);
    setError("");

    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      if (error) setError(error.message);
    } catch {
      setError("Unable to sign in right now. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-card">
        <header className="login-brand">
          <img className="login-logo" src={marshallLogo} width="240" height="163" alt="Marshall University" />
          <p className="login-product-name">Course Advisor</p>
          <p className="login-brand-description">Review your degree progress and plan your next semester.</p>
        </header>

        <section className="login-panel" aria-labelledby="login-title">
          <div className="login-panel-content">
            <h1 id="login-title">Student sign in</h1>
            <p className="login-intro">Use your Course Advisor account to continue.</p>

            <form className="login-form" aria-labelledby="login-title" onSubmit={handleLogin}>
              <div className="login-field">
                <label htmlFor="email">Email</label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  autoCapitalize="none"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>

              <div className="login-field">
                <label htmlFor="password">Password</label>
                <input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </div>

              {error && <p className="login-error" role="alert">{error}</p>}

              <button className="login-submit" type="submit" disabled={loading}>
                {loading ? "Signing in…" : "Sign In"}
              </button>
            </form>
            <p className="sr-only" role="status">{loading ? "Signing in…" : ""}</p>
          </div>
        </section>
      </div>
    </main>
  );
}

export default Login;
