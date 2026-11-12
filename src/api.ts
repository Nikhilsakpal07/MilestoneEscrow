export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

/**
 * Safe fetch with AbortController timeout to prevent UI hanging indefinitely.
 */
export async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs = 2500
): Promise<Response> {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch(url, {
      ...options,
      signal: controller.signal,
    })
    return res
  } finally {
    clearTimeout(timeoutId)
  }
}

export interface MilestoneData {
  id: number
  description: string
  deadline: string
  percentage: number
  amount_usd: number
  status: string
  authorization_ids?: string[]
  tracking_number?: string
  shipping_status?: string
  paypal_payout_batch_id?: string
  certificate_id?: string
  has_certificate?: boolean
  requires_human_approval?: boolean
}

export interface BountyData {
  id: number
  title: string
  acceptance_criteria: string
  amount_usd: number
  remaining_amount_usd: number
  freelancer_email: string
  freelancer_phone?: string
  worker_me_id?: string
  sponsor_me_id?: string
  sponsor_email?: string
  created_by_user_id?: string
  tracking_number?: string
  shipping_status?: string
  status: string
  risk_score?: "LOW" | "MEDIUM" | "HIGH"
  risk_percentage?: number
  risk_factors?: string[]
  requires_human_approval?: boolean
  is_high_value?: boolean
  milestones: MilestoneData[]
}

/**
 * Fetch bounties from backend API.
 * Optionally filter by ME-ID, Email, or Role (sponsor/worker).
 */
export async function getBounties(meId?: string, email?: string, role?: string): Promise<BountyData[]> {
  try {
    const params = new URLSearchParams()
    if (meId) params.append("me_id", meId)
    if (email) params.append("email", email)
    if (role) params.append("role", role)
    const qs = params.toString() ? `?${params.toString()}` : ""
    const res = await fetchWithTimeout(`${API_BASE}/api/bounties${qs}`, { method: "GET" }, 2500)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (Array.isArray(data)) {
      return data
    }
  } catch (err) {
    console.warn("[API] Could not fetch bounties from backend:", err)
  }
  return []
}

/**
 * Conversational Escrow Scope Generation via Gemini
 */
export async function generateScopeAI(prompt: string) {
  const res = await fetch(`${API_BASE}/api/bounties/generate-scope`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) throw new Error("Failed to generate AI contract scope")
  return res.json()
}

/**
 * Create a new contract on the backend
 */
export async function createBounty(payload: any) {
  const res = await fetch(`${API_BASE}/api/bounties`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}))
    throw new Error(errData.detail || "Failed to create escrow contract")
  }
  return res.json()
}

/**
 * Submit deliverable for AI audit (files or GitHub)
 */
export async function submitWork(
  bountyId: number,
  milestoneId: number,
  payload: { github_url?: string; file?: File }
) {
  let body: any
  let headers: HeadersInit = {}

  if (payload.file) {
    const formData = new FormData()
    formData.append("file", payload.file)
    formData.append("milestone_id", String(milestoneId))
    if (payload.github_url) formData.append("github_url", payload.github_url)
    body = formData
  } else {
    headers["Content-Type"] = "application/json"
    body = JSON.stringify({
      github_url: payload.github_url,
      milestone_id: milestoneId,
    })
  }

  const res = await fetch(
    `${API_BASE}/api/bounties/${bountyId}/milestones/${milestoneId}/submit-work`,
    {
      method: "POST",
      headers,
      body,
    }
  )

  const data = await res.json()
  if (!res.ok && res.status !== 400) {
    throw new Error(data.detail || "Failed to audit submission")
  }
  return data
}

/**
 * Submit appeal to Mediator AI
 */
export async function submitAppeal(
  bountyId: number,
  milestoneId: number,
  appeal_text: string
) {
  const res = await fetch(`${API_BASE}/api/bounties/${bountyId}/appeal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ appeal_text, milestone_id: milestoneId }),
  })
  return res.json()
}

/**
 * Run Gemini Predictive Risk Engine across active contracts
 */
export async function runPredictiveRiskEngine() {
  const res = await fetch(`${API_BASE}/api/analytics/predictive-risk/evaluate`, {
    method: "POST",
  })
  return res.json()
}

/**
 * Admin Multi-Sig Final Approval
 */
export async function adminApproveMilestone(
  bountyId: number,
  milestoneId: number,
  adminNotes: string
) {
  const res = await fetch(
    `${API_BASE}/api/admin/bounties/${bountyId}/milestones/${milestoneId}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_notes: adminNotes }),
    }
  )
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "Admin approval co-sign failed")
  }
  return res.json()
}

/**
 * Get Logistics Shipping Oracle Status
 */
export async function getShippingOracle(trackingNumber: string) {
  const res = await fetch(
    `${API_BASE}/api/oracle/shipping/${encodeURIComponent(trackingNumber)}`
  )
  return res.json()
}

/**
 * Simulate Carrier Update
 */
export async function updateShippingOracle(trackingNumber: string, status: string) {
  const res = await fetch(
    `${API_BASE}/api/oracle/shipping/${encodeURIComponent(trackingNumber)}/status`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }
  )
  return res.json()
}

/**
 * Poll Oracle immediately to trigger auto-approvals
 */
export async function pollOracleDeliveries() {
  const res = await fetch(`${API_BASE}/api/oracle/poll`, { method: "POST" })
  return res.json()
}

/**
 * Register a user with Firebase UID and Email.
 * Generates an Ethereum wallet address, stores keys, and returns the derived blockchain ME-ID.
 */
export async function registerAuthUser(firebaseUid: string, email: string, name?: string) {
  const res = await fetchWithTimeout(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      firebase_uid: firebaseUid,
      email,
      name,
    }),
  }, 2500)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "User registration failed.")
  }
  return res.json()
}

/**
 * Fetch the authenticated user's PostgreSQL record (including ME-ID & Ethereum wallet) using their Firebase JWT.
 */
export async function getAuthMe(token?: string) {
  const headers: Record<string, string> = {}
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }
  const res = await fetchWithTimeout(`${API_BASE}/api/auth/me`, {
    method: "GET",
    headers,
  }, 2500)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "Failed to fetch user profile from PostgreSQL.")
  }
  return res.json()
}

/**
 * Verify a Worker ME-ID against the PostgreSQL registry.
 */
export async function verifyWorkerMeId(meId: string) {
  const res = await fetchWithTimeout(
    `${API_BASE}/api/users/verify-me-id/${encodeURIComponent(meId.trim())}`,
    {
      method: "GET",
    },
    2500
  )
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Worker ME-ID '${meId}' is not registered in PostgreSQL.`)
  }
  return res.json()
}

/**
 * Create a PayPal Escrow order for a specific milestone.
 */
export async function createMilestonePayPalOrder(milestoneId: number | string, amount: number) {
  const res = await fetch(`${API_BASE}/api/milestones/${milestoneId}/create-order`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "Failed to initialize PayPal escrow order.")
  }
  return res.json()
}

/**
 * Capture an approved PayPal Escrow order for a specific milestone.
 */
export async function captureMilestonePayPalOrder(milestoneId: number | string, orderID: string) {
  const res = await fetch(`${API_BASE}/api/milestones/${milestoneId}/capture-order`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_id: orderID, orderID }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "Failed to capture PayPal escrow order.")
  }
  return res.json()
}



