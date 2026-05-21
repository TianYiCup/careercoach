/**
 * SMS login flow — PRD §F1 / §7.2 / design-spec §9.1.
 *
 * Two-step minimal UI per design-spec ("极简：1 个输入框 + 1 个按钮"):
 *   step 1 (phone): 11-digit input → POST /v1/auth/sms/send → step 2
 *   step 2 (code):  6-digit input  → POST /v1/auth/sms/verify → useAuth.login
 *
 * Inline errors are specific per PRD ("验证码错了，再试一次" instead
 * of "登录失败"). Resend cooldown matches the backend's 60s TTL.
 *
 * The whole component re-renders out of existence once login() lands —
 * the parent (<AppGate>) gates on isAuthenticated, so we don't navigate
 * explicitly.
 */

import { useEffect, useState } from 'react';

import { BlobBackground, GlassCard, MascotReaction } from '../../components';
import { apiClient, ApiError } from '../../api/v1';
import type { SmsSendResponse, SmsVerifyResponse } from '../../api/v1';
import { useAuth } from './useAuth';

// CN mainland mobile — matches backend SmsSendRequest pattern.
const PHONE_PATTERN = /^1[3-9]\d{9}$/;
const CODE_PATTERN = /^\d{6}$/;

type Step = 'phone' | 'code';

export function LoginPage() {
  const { login } = useAuth();
  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  // Resend cooldown ticker — `cooldown` is seconds remaining.
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(t);
  }, [cooldown]);

  const handleSend = async () => {
    if (!PHONE_PATTERN.test(phone)) {
      setError('请输入正确的手机号');
      return;
    }
    setError(null);
    setPending(true);
    try {
      const res = await apiClient.post<SmsSendResponse>('/auth/sms/send', { phone });
      setStep('code');
      setCode('');
      setCooldown(res.ttl);
    } catch (e) {
      const msg = _humanizeSendError(e);
      setError(msg);
      // B-8: Read Retry-After header on 429 to start cooldown
      if (e instanceof ApiError && e.status === 429) {
        const retryAfter = e.headers.get('Retry-After');
        if (retryAfter) {
          const secs = Number(retryAfter);
          if (secs > 0) setCooldown(secs);
        }
      }
    } finally {
      setPending(false);
    }
  };

  const handleVerify = async () => {
    if (!CODE_PATTERN.test(code)) {
      setError('验证码是 6 位数字');
      return;
    }
    setError(null);
    setPending(true);
    try {
      const res = await apiClient.post<SmsVerifyResponse>('/auth/sms/verify', {
        phone,
        code,
      });
      // login() updates context → <AppGate> swaps us out for HomePage.
      login(res.token, res.user);
    } catch (e) {
      setError(_humanizeVerifyError(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="relative min-h-screen flex flex-col items-center justify-center px-4 py-12 overflow-hidden">
      <BlobBackground />
      <div className="relative z-10 w-full max-w-sm space-y-6">
        <div className="text-center">
          <MascotReaction expression="confident" size="lg" showLabel />
          <h1 className="mt-4 text-3xl font-display italic text-gradient-vivid">
            CareerCoach AI
          </h1>
          <p className="mt-1 text-sm text-ink-text-2 font-body">
            {step === 'phone' ? '输入手机号开始对练' : '验证码已发送'}
          </p>
        </div>

        <GlassCard className="space-y-4">
          {step === 'phone' ? (
            <PhoneStep
              phone={phone}
              pending={pending}
              onChange={setPhone}
              onSubmit={handleSend}
            />
          ) : (
            <CodeStep
              phone={phone}
              code={code}
              pending={pending}
              cooldown={cooldown}
              onCodeChange={setCode}
              onSubmit={handleVerify}
              onResend={handleSend}
              onBack={() => {
                setStep('phone');
                setCode('');
                setError(null);
              }}
            />
          )}
          {error !== null && (
            <p
              className="text-sm text-vivid-orange text-center font-body"
              role="alert"
            >
              {error}
            </p>
          )}
        </GlassCard>

        <p className="text-xs text-ink-text-3 text-center px-4">
          登录即同意《用户协议》和《隐私政策》
        </p>
      </div>
    </div>
  );
}

interface PhoneStepProps {
  phone: string;
  pending: boolean;
  onChange: (v: string) => void;
  onSubmit: () => void;
}

function PhoneStep({ phone, pending, onChange, onSubmit }: PhoneStepProps) {
  const disabled = pending || !PHONE_PATTERN.test(phone);
  return (
    <>
      <input
        type="tel"
        inputMode="numeric"
        autoComplete="tel"
        maxLength={11}
        value={phone}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !disabled) onSubmit();
        }}
        placeholder="11 位手机号"
        aria-label="手机号"
        className="w-full rounded-radius-pill bg-ink-card px-5 py-3 text-base text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors"
      />
      <button
        type="button"
        onClick={onSubmit}
        disabled={disabled}
        className="w-full px-5 py-3 rounded-radius-pill gradient-vivid text-white text-base font-body font-medium hover:scale-[1.02] transition-transform disabled:opacity-50 disabled:hover:scale-100"
      >
        {pending ? '发送中…' : '发送验证码'}
      </button>
    </>
  );
}

interface CodeStepProps {
  phone: string;
  code: string;
  pending: boolean;
  cooldown: number;
  onCodeChange: (v: string) => void;
  onSubmit: () => void;
  onResend: () => void;
  onBack: () => void;
}

function CodeStep({
  phone,
  code,
  pending,
  cooldown,
  onCodeChange,
  onSubmit,
  onResend,
  onBack,
}: CodeStepProps) {
  const disabled = pending || !CODE_PATTERN.test(code);
  return (
    <>
      <p className="text-xs text-ink-text-3 text-center">
        验证码已发送至 {_maskPhone(phone)}
      </p>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        maxLength={6}
        value={code}
        onChange={(e) => onCodeChange(e.target.value.replace(/\D/g, ''))}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !disabled) onSubmit();
        }}
        placeholder="6 位验证码"
        aria-label="验证码"
        className="w-full rounded-radius-pill bg-ink-card px-5 py-3 text-base text-ink-text font-body border border-ink-line focus:border-vivid-purple focus:outline-none transition-colors text-center tracking-widest"
      />
      <button
        type="button"
        onClick={onSubmit}
        disabled={disabled}
        className="w-full px-5 py-3 rounded-radius-pill gradient-vivid text-white text-base font-body font-medium hover:scale-[1.02] transition-transform disabled:opacity-50 disabled:hover:scale-100"
      >
        {pending ? '登录中…' : '登录'}
      </button>
      <div className="flex items-center justify-between text-xs font-body">
        <button
          type="button"
          onClick={onBack}
          className="text-ink-text-3 hover:text-ink-text-2 transition-colors"
        >
          ← 换手机号
        </button>
        <button
          type="button"
          onClick={onResend}
          disabled={pending || cooldown > 0}
          className="text-vivid-purple hover:underline disabled:text-ink-text-3 disabled:no-underline"
        >
          {cooldown > 0 ? `重发 (${cooldown}s)` : '重新发送'}
        </button>
      </div>
    </>
  );
}

function _maskPhone(phone: string): string {
  if (phone.length < 7) return phone;
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
}

function _humanizeSendError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) return '手机号格式不对';
    if (e.status === 429) {
      const code = (e.body as { code?: string })?.code;
      if (code === 'SMS_SEND_COOLDOWN') {
        const retryAfter = e.headers.get('Retry-After');
        const secs = retryAfter ? Number(retryAfter) : 0;
        if (secs > 0) return `发送太频繁，${secs} 秒后再试`;
        return '发送太频繁，请稍后再试';
      }
      return '请求太频繁，稍后再试';
    }
  }
  return '发送失败，请稍后再试';
}

function _humanizeVerifyError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 400) return '验证码错了，再试一次';
    if (e.status === 429) {
      const code = (e.body as { code?: string })?.code;
      if (code === 'SMS_VERIFY_LOCKED') return '验证次数过多，请稍后再试';
      return '请求太频繁，稍后再试';
    }
  }
  return '登录失败，请稍后再试';
}
