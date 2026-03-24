import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { authApi } from '../services/api';

export default function ForgotPassword() {
    const [email, setEmail] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');

    useEffect(() => {
        document.documentElement.setAttribute('data-theme', 'dark');
    }, []);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setMessage('');
        setLoading(true);

        try {
            const res = await authApi.forgotPassword({ email: email.trim() });
            setMessage(res.message);
        } catch (err: any) {
            setError(err.message || 'Failed to request password reset');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="login-page">
            <div className="login-form-panel" style={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
                <div className="login-form-wrapper" style={{ maxWidth: '460px' }}>
                    <div className="login-form-header">
                        <div className="login-form-logo">
                            <img src="/logo-black.png" className="login-logo-img" alt="" style={{ width: 28, height: 28, marginRight: 8, verticalAlign: 'middle' }} />
                            Clawith
                        </div>
                        <h2 className="login-form-title">Forgot password</h2>
                        <p className="login-form-subtitle">
                            Enter your account email and we will send a reset link if the account exists.
                        </p>
                    </div>

                    {error && (
                        <div className="login-error">
                            <span>⚠</span> {error}
                        </div>
                    )}

                    {message && (
                        <div className="login-error" style={{ background: 'rgba(34,197,94,0.14)', borderColor: 'rgba(34,197,94,0.35)', color: '#dcfce7' }}>
                            <span>✓</span> {message}
                        </div>
                    )}

                    <form onSubmit={handleSubmit} className="login-form">
                        <div className="login-field">
                            <label>Email</label>
                            <input
                                type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                                autoFocus
                                placeholder="name@company.com"
                            />
                        </div>

                        <button className="login-submit" type="submit" disabled={loading || !email.trim()}>
                            {loading ? <span className="login-spinner" /> : 'Send reset link'}
                        </button>
                    </form>

                    <div className="login-switch">
                        Remembered your password? <Link to="/login">Back to login</Link>
                    </div>
                </div>
            </div>
        </div>
    );
}
