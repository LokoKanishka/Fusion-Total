#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_ROUTING_PROFILES = {
    "default": {
        "model": "openai-codex/gpt-5.1-codex-mini",
        "fallback_model": "qwen2.5-coder:14b-instruct-q8_0",
        "backend": "cloud",
        "mcp_profile": "safe_readonly",
    },
    "automation": {
        "model": "openai-codex/gpt-5.1-codex-mini",
        "fallback_model": "qwen2.5-coder:14b-instruct-q8_0",
        "backend": "cloud",
        "mcp_profile": "ops_automation",
    },
    "coding": {
        "model": "qwen2.5-coder:14b-instruct-q8_0",
        "fallback_model": "openai-codex/gpt-5.1-codex-mini",
        "backend": "local",
        "mcp_profile": "dev_build",
    },
    "research": {
        "model": "openai-codex/gpt-5.1-codex-mini",
        "fallback_model": "qwen2.5-coder:14b-instruct-q8_0",
        "backend": "cloud",
        "mcp_profile": "research_readonly",
    },
    "voice": {
        "model": "openai-codex/gpt-5.1-codex-mini",
        "fallback_model": "qwen2.5-coder:14b-instruct-q8_0",
        "backend": "cloud",
        "mcp_profile": "safe_readonly",
    },
}

DEFAULT_MCP_PROFILE_MATRIX = {
    "safe_readonly": {
        "approval_mode": "manual_for_writes",
        "servers": ["community-cloudflare", "community-exa", "community-arxiv"],
        "sensitive_tools": [],
        "notes": "readonly",
    },
    "research_readonly": {
        "approval_mode": "manual_for_writes",
        "servers": ["community-cloudflare", "community-exa", "community-arxiv", "community-notion"],
        "sensitive_tools": [],
        "notes": "research",
    },
    "ops_automation": {
        "approval_mode": "always_for_writes",
        "servers": ["community-n8n", "community-github", "community-cloudflare"],
        "sensitive_tools": ["workflow_update", "workflow_delete", "repo_push", "deploy"],
        "notes": "automation",
    },
    "dev_build": {
        "approval_mode": "always_for_writes",
        "servers": ["community-github", "community-browserbase", "community-n8n"],
        "sensitive_tools": ["repo_push", "workflow_publish", "browser_run_with_credentials"],
        "notes": "build",
    },
}

DEFAULT_ACTION_POLICIES = {
    "default_requirements": {
        "read": "none",
        "write": "human_approval",
        "sensitive": "human_approval",
    },
    "action_rules": {
        "workflow_list": "read",
        "workflow_get": "read",
        "workflow_run": "write",
        "workflow_create": "write",
        "workflow_update": "write",
        "workflow_publish": "sensitive",
        "workflow_delete": "sensitive",
        "repo_read": "read",
        "repo_search": "read",
        "repo_push": "sensitive",
        "deploy": "sensitive",
        "notion_read": "read",
        "notion_write": "write",
        "credential_read": "sensitive",
        "credential_write": "sensitive",
    },
    "keyword_fallback": {
        "sensitive": ["delete", "destroy", "drop", "credential", "secret", "token", "billing", "publish", "deploy", "push"],
        "write": ["create", "update", "write", "patch", "edit", "insert", "append", "run"],
    },
}

DEFAULT_APPROVAL_POLICY = {
    "required_fields": {
        "write": ["approved_by", "approval_token", "approval_ts", "approval_scope"],
        "sensitive": ["approved_by", "approval_token", "approval_ts", "approval_scope", "approval_justification", "approval_change_ticket"],
    },
    "token_pattern": r"^[A-Za-z0-9._:-]{8,128}$",
    "ticket_pattern": r"^(CHG|INC|TASK)-[A-Za-z0-9_-]{3,64}$",
    "max_age_seconds": {
        "write": 1800,
        "sensitive": 900,
    },
    "require_scope_match": True,
    "hmac": {
        "enabled": True,
        "algorithm": "sha256",
        "secret_env_var": "MCP_APPROVAL_HMAC_SECRET",
        "field": "approval_sig",
        "require_for_levels": ["write", "sensitive"],
    },
}

CODE_JS_TEMPLATE = r'''
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROUTING_PROFILES = __ROUTING_PROFILES__;
const MCP_PROFILE_MATRIX = __MCP_PROFILE_MATRIX__;
const ACTION_POLICIES = __ACTION_POLICIES__;
const APPROVAL_POLICY = __APPROVAL_POLICY__;
const PATCH_HMAC_SECRET = __PATCH_HMAC_SECRET__;
const MODEL_ALLOWLIST = [
  'openai-codex/gpt-5.1-codex-mini',
  'qwen2.5-coder:14b-instruct-q8_0',
];
const DEFAULT_MODEL = 'openai-codex/gpt-5.1-codex-mini';
const DEFAULT_FALLBACK_MODEL = 'qwen2.5-coder:14b-instruct-q8_0';
const DEFAULT_PROFILE = Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, 'default')
  ? 'default'
  : (Object.keys(ROUTING_PROFILES)[0] || 'default');
const DEFAULT_MCP_PROFILE = Object.prototype.hasOwnProperty.call(MCP_PROFILE_MATRIX, 'safe_readonly')
  ? 'safe_readonly'
  : (Object.keys(MCP_PROFILE_MATRIX)[0] || '');

function isObject(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

function cleanString(v) {
  if (typeof v !== 'string') return '';
  return v.trim();
}

function normalizeCorrelationId(rawBody) {
  const cid = rawBody.correlation_id;
  if (typeof cid === 'string' && cid.length >= 8 && cid.length <= 128) {
    return cid;
  }
  const payloadHash = crypto.createHash('sha256').update(JSON.stringify(rawBody)).digest('hex').slice(0, 24);
  return `cid_${payloadHash}`;
}

function normalizeBackend(raw) {
  const v = cleanString(raw).toLowerCase();
  if (v === 'local') return 'local';
  return 'cloud';
}

function pickAllowedModel(candidate, fallback) {
  const c = cleanString(candidate);
  if (MODEL_ALLOWLIST.includes(c)) return c;
  return fallback;
}

function inferProfile(body, meta) {
  const explicit = cleanString(meta.workflow_profile || meta.route_profile || meta.profile).toLowerCase();
  if (explicit && Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, explicit)) {
    return explicit;
  }
  const kind = cleanString(body.kind).toLowerCase();
  const source = cleanString(body.source).toLowerCase();
  const taskType = cleanString(meta.task_type || meta.intent || '').toLowerCase();
  const text = cleanString(body.text).toLowerCase();

  if (
    ['code', 'coding', 'build', 'debug', 'patch', 'refactor', 'script'].includes(taskType) ||
    /\b(codigo|code|debug|patch|refactor|script)\b/.test(text)
  ) {
    return Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, 'coding') ? 'coding' : DEFAULT_PROFILE;
  }
  if (kind === 'voice' || source.startsWith('voice') || source.includes('stt')) {
    return Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, 'voice') ? 'voice' : DEFAULT_PROFILE;
  }
  if (
    ['research', 'search', 'analysis'].includes(taskType) ||
    kind === 'search' ||
    /\b(investigar|research|buscar|analisis)\b/.test(text)
  ) {
    return Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, 'research') ? 'research' : DEFAULT_PROFILE;
  }
  if (source.includes('n8n') || taskType.includes('workflow') || taskType.includes('automation')) {
    return Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, 'automation') ? 'automation' : DEFAULT_PROFILE;
  }
  return DEFAULT_PROFILE;
}

function resolveRoute(profileName) {
  const profile = Object.prototype.hasOwnProperty.call(ROUTING_PROFILES, profileName) ? profileName : DEFAULT_PROFILE;
  const conf = isObject(ROUTING_PROFILES[profile]) ? ROUTING_PROFILES[profile] : {};
  const model = pickAllowedModel(conf.model, DEFAULT_MODEL);
  const fallbackModel = pickAllowedModel(conf.fallback_model, DEFAULT_FALLBACK_MODEL);
  const backend = normalizeBackend(conf.backend);
  return {
    profile,
    model,
    fallback_model: fallbackModel,
    backend,
    mcp_profile: cleanString(conf.mcp_profile) || DEFAULT_MCP_PROFILE
  };
}

function resolveMcpPolicy(meta, route) {
  const explicit = cleanString(meta.mcp_profile).toLowerCase();
  const profile = explicit && Object.prototype.hasOwnProperty.call(MCP_PROFILE_MATRIX, explicit)
    ? explicit
    : (Object.prototype.hasOwnProperty.call(MCP_PROFILE_MATRIX, route.mcp_profile)
        ? route.mcp_profile
        : DEFAULT_MCP_PROFILE);
  const conf = isObject(MCP_PROFILE_MATRIX[profile]) ? MCP_PROFILE_MATRIX[profile] : {};
  const servers = Array.isArray(conf.servers)
    ? conf.servers.map((v) => cleanString(v)).filter(Boolean)
    : [];
  const sensitive = Array.isArray(conf.sensitive_tools)
    ? conf.sensitive_tools.map((v) => cleanString(v)).filter(Boolean)
    : [];
  return {
    profile,
    approval_mode: cleanString(conf.approval_mode) || 'manual_for_writes',
    servers,
    sensitive_tools: sensitive,
    notes: cleanString(conf.notes),
  };
}

function normalizeActionList(meta) {
  const raw = meta.mcp_actions || meta.actions;
  if (Array.isArray(raw)) {
    return raw.map((v) => cleanString(v).toLowerCase()).filter(Boolean);
  }
  if (typeof raw === 'string') {
    return raw
      .split(',')
      .map((v) => cleanString(v).toLowerCase())
      .filter(Boolean);
  }
  return [];
}

function classifyAction(action) {
  const rules = isObject(ACTION_POLICIES.action_rules) ? ACTION_POLICIES.action_rules : {};
  if (typeof rules[action] === 'string') {
    const level = cleanString(rules[action]).toLowerCase();
    if (['read', 'write', 'sensitive'].includes(level)) return level;
  }
  const fallback = isObject(ACTION_POLICIES.keyword_fallback) ? ACTION_POLICIES.keyword_fallback : {};
  const sensitiveKeywords = Array.isArray(fallback.sensitive) ? fallback.sensitive : [];
  const writeKeywords = Array.isArray(fallback.write) ? fallback.write : [];
  for (const kw of sensitiveKeywords) {
    const k = cleanString(kw).toLowerCase();
    if (k && action.includes(k)) return 'sensitive';
  }
  for (const kw of writeKeywords) {
    const k = cleanString(kw).toLowerCase();
    if (k && action.includes(k)) return 'write';
  }
  return 'read';
}

function requiresHumanApproval(level, approvalMode) {
  const mode = cleanString(approvalMode).toLowerCase();
  if (level === 'read') return false;
  if (mode === 'none') return false;
  if (mode === 'always_for_sensitive') return level === 'sensitive';
  if (mode === 'always_for_writes' || mode === 'manual_for_writes') return level === 'write' || level === 'sensitive';
  if (mode === 'read_only') return level !== 'read';
  return level === 'write' || level === 'sensitive';
}

function normalizeScope(meta) {
  const raw = meta.approval_scope;
  if (Array.isArray(raw)) {
    return raw.map((v) => cleanString(v).toLowerCase()).filter(Boolean);
  }
  if (typeof raw === 'string') {
    return raw.split(',').map((v) => cleanString(v).toLowerCase()).filter(Boolean);
  }
  return [];
}

function parseIsoToMs(value) {
  const v = cleanString(value);
  if (!v) return null;
  const ms = Date.parse(v);
  if (Number.isNaN(ms)) return null;
  return ms;
}

function isTokenValid(token) {
  const pattern = cleanString(APPROVAL_POLICY.token_pattern);
  if (!pattern) return !!token;
  try {
    const re = new RegExp(pattern);
    return re.test(token);
  } catch (_) {
    return !!token;
  }
}

function isTicketValid(ticket) {
  const pattern = cleanString(APPROVAL_POLICY.ticket_pattern);
  if (!pattern) return !!ticket;
  try {
    const re = new RegExp(pattern);
    return re.test(ticket);
  } catch (_) {
    return !!ticket;
  }
}

function requiredFieldsForLevel(level) {
  const req = isObject(APPROVAL_POLICY.required_fields) ? APPROVAL_POLICY.required_fields : {};
  const fields = req[level];
  if (Array.isArray(fields)) {
    return fields.map((v) => cleanString(v)).filter(Boolean);
  }
  return ['approved_by', 'approval_token'];
}

function maxAgeSecondsForLevel(level) {
  const maxAge = isObject(APPROVAL_POLICY.max_age_seconds) ? APPROVAL_POLICY.max_age_seconds : {};
  const v = Number(maxAge[level]);
  if (Number.isFinite(v) && v > 0) return v;
  return level === 'sensitive' ? 900 : 1800;
}

function hmacConfig() {
  const conf = isObject(APPROVAL_POLICY.hmac) ? APPROVAL_POLICY.hmac : {};
  const enabled = conf.enabled !== false;
  const algorithm = cleanString(conf.algorithm).toLowerCase() || 'sha256';
  const secretEnvVar = cleanString(conf.secret_env_var) || 'MCP_APPROVAL_HMAC_SECRET';
  const field = cleanString(conf.field) || 'approval_sig';
  const requireForLevels = Array.isArray(conf.require_for_levels)
    ? conf.require_for_levels.map((v) => cleanString(v).toLowerCase()).filter(Boolean)
    : ['write', 'sensitive'];
  return { enabled, algorithm, secretEnvVar, field, requireForLevels };
}

function signatureCanonicalString(params) {
  const actions = Array.isArray(params.actions) ? [...params.actions].sort() : [];
  const scope = Array.isArray(params.scope) ? [...params.scope].sort() : [];
  return [
    cleanString(params.approvedBy),
    cleanString(params.approvalToken),
    cleanString(params.approvalTs),
    cleanString(params.correlationId),
    cleanString(params.level),
    actions.join(','),
    scope.join(','),
    cleanString(params.approvalChangeTicket),
    cleanString(params.approvalJustification),
  ].join('\n');
}

function getEnvVar(name) {
  const key = cleanString(name);
  if (!key) return '';
  try {
    if (typeof $env !== 'undefined' && $env && typeof $env === 'object') {
      const v = $env[key];
      if (v !== undefined && v !== null) return String(v);
    }
  } catch (_) {
    // ignore
  }
  return '';
}

function verifyApprovalSignature(params) {
  const conf = hmacConfig();
  if (!conf.enabled || !conf.requireForLevels.includes(params.level)) {
    return { required: false, ok: true, reason: '', provided: false };
  }
  const providedSig = cleanString(params.approvalSig || '');
  if (!providedSig) {
    return { required: true, ok: false, reason: 'missing_approval_sig', provided: false };
  }
  if (conf.algorithm !== 'sha256') {
    return { required: true, ok: false, reason: 'unsupported_hmac_algorithm', provided: true };
  }
  const secret = cleanString(getEnvVar(conf.secretEnvVar) || PATCH_HMAC_SECRET);
  if (!secret) {
    return { required: true, ok: false, reason: 'missing_hmac_secret_server', provided: true };
  }
  const canonical = signatureCanonicalString(params);
  const expectedSig = crypto.createHmac('sha256', secret).update(canonical, 'utf8').digest('hex');
  const a = Buffer.from(providedSig, 'utf8');
  const b = Buffer.from(expectedSig, 'utf8');
  if (a.length !== b.length) {
    return { required: true, ok: false, reason: 'invalid_approval_sig', provided: true };
  }
  const ok = crypto.timingSafeEqual(a, b);
  return {
    required: true,
    ok,
    reason: ok ? '' : 'invalid_approval_sig',
    provided: true,
  };
}

function evaluateMcpApproval(meta, mcpPolicy, receivedTs, correlationId) {
  const actions = normalizeActionList(meta);
  const approvalToken = cleanString(meta.approval_token || meta.human_approval_token);
  const approvedBy = cleanString(meta.approved_by || meta.human_approved_by);
  const approvalTs = cleanString(meta.approval_ts || meta.human_approval_ts);
  const approvalJustification = cleanString(meta.approval_justification || meta.human_approval_justification);
  const approvalChangeTicket = cleanString(meta.approval_change_ticket || meta.change_ticket || meta.ticket_id);
  const sigField = cleanString(hmacConfig().field) || 'approval_sig';
  const approvalSig = cleanString(meta[sigField] || meta.approval_sig || meta.human_approval_sig);
  const scope = normalizeScope(meta);
  const actionEntries = actions.map((action) => {
    const level = classifyAction(action);
    return {
      action,
      level,
      requires_approval: requiresHumanApproval(level, mcpPolicy.approval_mode),
    };
  });
  let highestLevel = 'read';
  if (actionEntries.some((x) => x.level === 'sensitive')) highestLevel = 'sensitive';
  else if (actionEntries.some((x) => x.level === 'write')) highestLevel = 'write';
  const requiresApproval = actionEntries.some((x) => x.requires_approval);
  const reasons = [];
  let hasApproval = true;

  if (requiresApproval) {
    hasApproval = Boolean(approvalToken && approvedBy);
    if (!approvedBy) reasons.push('missing_approved_by');
    if (!approvalToken) reasons.push('missing_approval_token');
    if (approvalToken && !isTokenValid(approvalToken)) reasons.push('invalid_approval_token_format');

    const requiredFields = requiredFieldsForLevel(highestLevel);
    for (const f of requiredFields) {
      if (f === 'approval_ts' && !approvalTs) reasons.push('missing_approval_ts');
      if (f === 'approval_scope' && scope.length === 0) reasons.push('missing_approval_scope');
      if (f === 'approval_justification' && !approvalJustification) reasons.push('missing_approval_justification');
      if (f === 'approval_change_ticket' && !approvalChangeTicket) reasons.push('missing_approval_change_ticket');
    }
    if (approvalChangeTicket && !isTicketValid(approvalChangeTicket)) reasons.push('invalid_approval_change_ticket_format');

    const approvalMs = parseIsoToMs(approvalTs);
    const nowMs = parseIsoToMs(receivedTs);
    if (approvalTs && approvalMs === null) reasons.push('invalid_approval_ts');
    if (approvalMs !== null && nowMs !== null) {
      const ageSec = Math.floor((nowMs - approvalMs) / 1000);
      const maxAgeSec = maxAgeSecondsForLevel(highestLevel);
      if (ageSec < 0) reasons.push('approval_ts_in_future');
      if (ageSec > maxAgeSec) reasons.push('approval_expired');
    }

    const requireScopeMatch = Boolean(APPROVAL_POLICY.require_scope_match);
    if (requireScopeMatch && scope.length > 0) {
      for (const entry of actionEntries) {
        if (!entry.requires_approval) continue;
        if (!scope.includes(entry.action)) reasons.push(`scope_missing:${entry.action}`);
      }
    }

    const sigCheck = verifyApprovalSignature({
      approvalSig,
      approvedBy,
      approvalToken,
      approvalTs,
      correlationId,
      level: highestLevel,
      actions: actionEntries.map((x) => x.action),
      scope,
      approvalChangeTicket,
      approvalJustification,
    });
    if (sigCheck.required && !sigCheck.ok) {
      reasons.push(sigCheck.reason || 'invalid_approval_sig');
    }
  }

  if (requiresApproval && reasons.length > 0) hasApproval = false;
  const blocked = requiresApproval && !hasApproval;
  return {
    actions: actionEntries,
    highest_level: highestLevel,
    requires_human_approval: requiresApproval,
    approved: !requiresApproval || hasApproval,
    blocked,
    approved_by: approvedBy,
    approval_token_present: Boolean(approvalToken),
    approval_sig_present: Boolean(approvalSig),
    approval_ts: approvalTs,
    approval_scope: scope,
    approval_change_ticket: approvalChangeTicket,
    approval_change_ticket_present: Boolean(approvalChangeTicket),
    approval_justification_present: Boolean(approvalJustification),
    reasons,
  };
}

function appendGatewayMetric(ev) {
  try {
    const metricsDir = '/data/lucy_ipc/metrics';
    const metricsPath = path.join(metricsDir, 'lucy_gateway_events.jsonl');
    fs.mkdirSync(metricsDir, { recursive: true });
    fs.appendFileSync(metricsPath, JSON.stringify(ev) + '\n', 'utf8');
  } catch (_) {
    // metric write is best effort
  }
}

const body = isObject($json.body) ? $json.body : {};
const headers = isObject($json.headers) ? $json.headers : {};
const receivedTs = new Date().toISOString();
const correlationId = normalizeCorrelationId(body);
const meta = isObject(body.meta) ? body.meta : {};
const routeProfile = inferProfile(body, meta);
const route = resolveRoute(routeProfile);
const mcpPolicy = resolveMcpPolicy(meta, route);
const mcpApproval = evaluateMcpApproval(meta, mcpPolicy, receivedTs, correlationId);
const enrichedMeta = {
  ...meta,
  routing: route,
  mcp: mcpPolicy,
  mcp_approval: mcpApproval,
  ingress: {
    gateway: 'Lucy_Gateway_v1',
    received_ts: receivedTs
  }
};
const normalizedPayload = {
  ...body,
  meta: enrichedMeta
};

const inboxDir = '/data/lucy_ipc/inbox';
const deadletterDir = '/data/lucy_ipc/deadletter';

const sourceIp = headers['x-forwarded-for'] || headers['x-real-ip'] || $json.ip || '';
const subset = {
  'user-agent': headers['user-agent'] || '',
  'content-type': headers['content-type'] || '',
  'x-request-id': headers['x-request-id'] || ''
};

const envelope = {
  version: 'v1',
  correlation_id: correlationId,
  received_ts: receivedTs,
  payload: normalizedPayload,
  headers_subset: subset,
  source_ip: sourceIp,
  status: 'accepted',
  routing: route,
  mcp: mcpPolicy,
  mcp_approval: mcpApproval
};

const errors = [];
if (typeof normalizedPayload.kind !== 'string' || !normalizedPayload.kind.trim()) errors.push('kind is required string');
if (typeof normalizedPayload.source !== 'string' || !normalizedPayload.source.trim()) errors.push('source is required string');
if (typeof normalizedPayload.ts !== 'string' || Number.isNaN(Date.parse(normalizedPayload.ts))) errors.push('ts must be RFC3339 date-time');
if (Object.prototype.hasOwnProperty.call(normalizedPayload, 'text') && typeof normalizedPayload.text !== 'string') errors.push('text must be string');
if (Object.prototype.hasOwnProperty.call(normalizedPayload, 'meta') && !isObject(normalizedPayload.meta)) errors.push('meta must be object');
if (mcpApproval.blocked) {
  const detail = Array.isArray(mcpApproval.reasons) && mcpApproval.reasons.length
    ? `:${mcpApproval.reasons.join(',')}`
    : '';
  errors.push(`human_approval_required_for_mcp_actions${detail}`);
}

const inboxPath = path.join(inboxDir, `${correlationId}.json`);
const deadletterPath = path.join(deadletterDir, `${correlationId}.json`);

let ack = {
  ok: true,
  correlation_id: correlationId,
  received_ts: receivedTs,
  status: 'accepted',
  next: `ipc://inbox/${correlationId}.json`,
  outbox_path: `ipc://outbox/${correlationId}.json`,
  outbox_contract: 'lucy_output_v1'
};

try {
  fs.mkdirSync(inboxDir, { recursive: true });
  fs.mkdirSync(deadletterDir, { recursive: true });

  if (fs.existsSync(inboxPath)) {
    envelope.status = 'duplicate';
    ack.next = `ipc://inbox/${correlationId}.json`;
  } else if (errors.length > 0) {
    envelope.status = 'deadletter';
    envelope.reason = `invalid_contract: ${errors.join('; ')}`;
    fs.writeFileSync(deadletterPath, JSON.stringify(envelope, null, 2) + '\n', { encoding: 'utf-8' });
    ack.ok = false;
    ack.reason = envelope.reason;
    ack.next = `ipc://deadletter/${correlationId}.json`;
  } else {
    fs.writeFileSync(inboxPath, JSON.stringify(envelope, null, 2) + '\n', { encoding: 'utf-8' });
  }
} catch (err) {
  const reason = `ipc_write_failed: ${err && err.message ? err.message : String(err)}`;
  envelope.status = 'deadletter';
  envelope.reason = reason;
  ack.ok = false;
  ack.reason = reason;
  ack.next = `ipc://deadletter/${correlationId}.json`;
  try {
    fs.mkdirSync(deadletterDir, { recursive: true });
    fs.writeFileSync(deadletterPath, JSON.stringify(envelope, null, 2) + '\n', { encoding: 'utf-8' });
  } catch (_) {
    // keep ACK deterministic even when deadletter write fails
  }
}

appendGatewayMetric({
  ts: receivedTs,
  correlation_id: correlationId,
  ingress_status: envelope.status,
  ack_ok: ack.ok,
  route_profile: route.profile,
  model: route.model,
  fallback_model: route.fallback_model,
  backend: route.backend,
  mcp_profile: mcpPolicy.profile,
  mcp_highest_action_level: mcpApproval.highest_level,
  mcp_requires_human_approval: mcpApproval.requires_human_approval,
  mcp_approval_blocked: mcpApproval.blocked,
  mcp_approval_change_ticket_present: mcpApproval.approval_change_ticket_present,
  mcp_approval_reasons: Array.isArray(mcpApproval.reasons) ? mcpApproval.reasons : [],
  source: cleanString(normalizedPayload.source),
  kind: cleanString(normalizedPayload.kind)
});

return [{ json: ack }];
'''.strip()


def ensure_workflow(obj):
    if isinstance(obj, list):
        if not obj:
            raise SystemExit("empty workflow list")
        return obj[0], True
    return obj, False


def load_json_or_fallback(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid json in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"invalid json root in {path}: expected object")
    return data


def build_code_js(repo_root: Path) -> str:
    routing = load_json_or_fallback(repo_root / "config" / "n8n_flow_routing.json", DEFAULT_ROUTING_PROFILES)
    mcp_matrix = load_json_or_fallback(repo_root / "config" / "n8n_mcp_matrix.json", DEFAULT_MCP_PROFILE_MATRIX)
    action_policies = load_json_or_fallback(repo_root / "config" / "mcp_action_policies.json", DEFAULT_ACTION_POLICIES)
    approval_policy = load_json_or_fallback(repo_root / "config" / "mcp_approval_policy.json", DEFAULT_APPROVAL_POLICY)
    patch_hmac_secret = os.getenv("MCP_APPROVAL_HMAC_SECRET", "fusion-local-approval-hmac-secret")
    return (
        CODE_JS_TEMPLATE
        .replace("__ROUTING_PROFILES__", json.dumps(routing, ensure_ascii=False, separators=(",", ":")))
        .replace("__MCP_PROFILE_MATRIX__", json.dumps(mcp_matrix, ensure_ascii=False, separators=(",", ":")))
        .replace("__ACTION_POLICIES__", json.dumps(action_policies, ensure_ascii=False, separators=(",", ":")))
        .replace("__APPROVAL_POLICY__", json.dumps(approval_policy, ensure_ascii=False, separators=(",", ":")))
        .replace("__PATCH_HMAC_SECRET__", json.dumps(patch_hmac_secret, ensure_ascii=False))
    )


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: patch_lucy_gateway_v1.py <input.json> <output.json>", file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    repo_root = Path(__file__).resolve().parents[1]
    code_js = build_code_js(repo_root)

    raw = json.loads(src.read_text(encoding="utf-8"))
    wf, wrapped = ensure_workflow(raw)

    nodes = wf.get("nodes") or []

    webhook_nodes = [node for node in nodes if node.get("type") == "n8n-nodes-base.webhook"]
    if not webhook_nodes:
        raise SystemExit("workflow does not contain required webhook node")

    code_name = "Gateway Contract + IPC"
    code_node = {
        "parameters": {
            "mode": "runOnceForAllItems",
            "jsCode": code_js,
        },
        "id": "lucy-gateway-code-v1",
        "name": code_name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [800, 300],
    }

    remove_names = {
        code_name,
        "ACK + Write Payload",
        "Gateway Prepare v1",
        "Gateway IPC Writer v1",
        "Gateway ACK v1",
    }
    remove_types = {
        "n8n-nodes-base.respondToWebhook",
        "n8n-nodes-base.executeCommand",
    }
    clean_nodes = [
        n for n in nodes
        if n.get("name") not in remove_names and n.get("type") not in remove_types
    ]
    clean_nodes.append(code_node)

    connections = {}
    for webhook in webhook_nodes:
        webhook_name = webhook["name"]
        webhook_params = webhook.get("parameters") or {}
        webhook_params["responseMode"] = "lastNode"
        webhook["parameters"] = webhook_params
        connections[webhook_name] = {
            "main": [[{"node": code_name, "type": "main", "index": 0}]],
        }

    wf["nodes"] = clean_nodes
    wf["connections"] = connections

    out_obj = [wf] if wrapped else wf
    dst.write_text(json.dumps(out_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PATCH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
