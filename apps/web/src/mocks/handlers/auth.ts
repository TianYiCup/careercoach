/**
 * MSW handlers for `/v1/auth/sms/{send,verify}` — PRD §7.2.
 *
 * Dev-only mock; the real backend uses Redis-backed codes + Aliyun SMS.
 *
 * Behavior:
 *   - `send`  validates the phone regex, generates a 6-digit code,
 *     logs it to the console (so dev can copy it) and stores it in a
 *     module-scoped Map. Returns ttl=60 to match server contract.
 *   - `verify` pops the code from the Map; if the supplied code
 *     matches we mint a fake JWT-shaped string and return a
 *     `UserPublic`. Any wrong code returns 400 INVALID_CODE with the
 *     same envelope the backend uses, so the UI's error branch is
 *     exercisable without a real backend.
 *
 * Dev escape hatch: if the user calls /verify with `123456` AND no
 * code was ever issued for the phone (e.g. they typed the code first),
 * we accept it — saves devs from re-running the send step every time
 * they reload.
 */

import { delay, http, HttpResponse } from 'msw';
import type {
  SmsSendRequest,
  SmsSendResponse,
  SmsVerifyRequest,
  SmsVerifyResponse,
} from '../../api/v1/types';

const BASE = '*/v1';
const PHONE_PATTERN = /^1[3-9]\d{9}$/;
const CODE_PATTERN = /^\d{6}$/;
const DEV_FALLBACK_CODE = '123456';

const issuedCodes = new Map<string, string>();

function _newCode(): string {
  return String(Math.floor(100000 + Math.random() * 900000));
}

interface ErrorEnvelope {
  code: string;
  message: string;
  trace_id: string;
}

function _errorEnvelope(
  status: number,
  code: string,
  message: string,
): HttpResponse<ErrorEnvelope> {
  return HttpResponse.json(
    { code, message, trace_id: `trc_dev_${Date.now().toString(36)}` },
    { status },
  );
}

export const authHandlers = [
  http.post(`${BASE}/auth/sms/send`, async ({ request }) => {
    await delay(200);
    const body = (await request.json()) as SmsSendRequest;
    if (!PHONE_PATTERN.test(body.phone)) {
      return _errorEnvelope(
        400,
        'BAD_REQUEST',
        'phone must be 11 digits starting with 1',
      );
    }
    const code = _newCode();
    issuedCodes.set(body.phone, code);
    // Console banner so devs can copy the code without grepping logs.
    // eslint-disable-next-line no-console
    console.info(
      `%c[MSW] /auth/sms/send  phone=${body.phone}  code=${code}  (or use ${DEV_FALLBACK_CODE})`,
      'color: #a855f7; font-weight: bold',
    );
    const res: SmsSendResponse = { ttl: 60 };
    return HttpResponse.json(res);
  }),

  http.post(`${BASE}/auth/sms/verify`, async ({ request }) => {
    await delay(300);
    const body = (await request.json()) as SmsVerifyRequest;
    if (!PHONE_PATTERN.test(body.phone) || !CODE_PATTERN.test(body.code)) {
      return _errorEnvelope(
        400,
        'INVALID_CODE',
        'phone or code format invalid',
      );
    }
    const expected = issuedCodes.get(body.phone);
    const matches = expected !== undefined && expected === body.code;
    const usingFallback = expected === undefined && body.code === DEV_FALLBACK_CODE;
    if (!matches && !usingFallback) {
      return _errorEnvelope(400, 'INVALID_CODE', 'code does not match');
    }
    // One-shot retire — same semantic as backend's CodeStore.pop.
    issuedCodes.delete(body.phone);

    const tail = body.phone.slice(-4);
    const res: SmsVerifyResponse = {
      token: `eyJ.dev-mock.${tail}.${Date.now().toString(36)}`,
      user: {
        id: `u_dev_${tail}`,
        nickname: `K 学员 ${tail}`,
        persona_type: 'in_school',
        is_minor: false,
      },
    };
    return HttpResponse.json(res);
  }),
];
