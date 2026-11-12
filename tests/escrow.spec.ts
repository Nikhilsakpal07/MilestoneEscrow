import { test, expect } from "@playwright/test"

/**
 * =========================================================================
 * MilestoneEscrow Platform - Enterprise End-to-End Test Suite
 * Standard: SOC 2 Type II Dual-Custody & Autonomous AI Arbitration
 * Framework: Next.js + FastAPI + Google Gemini 2.5 Pro + PayPal Multi-Sig
 * =========================================================================
 */

test.describe("MilestoneEscrow E2E Verification Suite", () => {
  // Shared mock bounties seed dataset
  const mockBounties = [
    {
      id: 42,
      title: "Autonomous Algorithmic Liquidity Router",
      acceptance_criteria: "Zero slippage execution & ISO-20022 compliance",
      amount_usd: 4500,
      remaining_amount_usd: 4500,
      freelancer_email: "elena.rostova@quantum-dev.de",
      freelancer_phone: "ME-8492-X",
      worker_me_id: "ME-8492-X",
      sponsor_me_id: "ME-SPON-8492",
      status: "LOCKED",
      risk_score: "LOW",
      risk_percentage: 12,
      milestones: [
        {
          id: 91,
          bounty_id: 42,
          description: "Phase 1: Core Routing Engine & Smart Relays",
          amount_usd: 4500,
          deadline: "2026-12-01",
          percentage: 100,
          status: "LOCKED",
          criteria_strings: [
            "ISO-20022 Schema Validation",
            "Zero Packet Loss under 10k req/s",
          ],
        },
      ],
    },
  ]

  test.beforeEach(async ({ page }) => {
    // Inject E2E mode so browser authentication hooks operate deterministically
    await page.addInitScript(() => {
      ;(window as any).__E2E_MODE__ = true
    })

    // Intercept default GET /api/bounties to deliver deterministic test contracts
    await page.route("**/api/bounties", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockBounties),
        })
      } else {
        await route.continue()
      }
    })

    // Mock Google Identity Toolkit / Firebase Auth REST APIs
    await page.route("**/identitytoolkit.googleapis.com/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "identitytoolkit#VerifyPasswordResponse",
          localId: "usr_mock_alex_8492",
          email: "alex.chen@fintech-dev.io",
          displayName: "Alex Chen",
          idToken: "mock-firebase-jwt-token-xyz-123",
          registered: true,
          refreshToken: "mock-refresh-token-456",
          expiresIn: "3600",
        }),
      })
    })

    // Mock /api/auth/me and /api/auth/register endpoints
    await page.route("**/api/auth/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          user: {
            id: "e2fae178-b91f-4c3b-b0ce-bc959e0fe965",
            firebase_uid: "usr_mock_alex_8492",
            email: "alex.chen@fintech-dev.io",
            wallet_address: "0x30782F4E7A81C0E72D2e18c98f5D4A22B4f30039",
            me_id: "ME-8492-X",
            created_at: new Date().toISOString(),
          },
        }),
      })
    })

    // Mock /api/users/verify-me-id/* endpoint
    await page.route("**/api/users/verify-me-id/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          valid: true,
          me_id: "ME-8492-X",
          email: "alex.chen@fintech-dev.io",
          wallet_address: "0x30782F4E7A81C0E72D2e18c98f5D4A22B4f30039",
          kyc_status: "VERIFIED",
          message: "Worker ME-ID verified in PostgreSQL registry.",
        }),
      })
    })
  })

  // =========================================================================
  // Phase 1: Identity & Dual-Role Navigation
  // =========================================================================
  test("Phase 1: Identity & Dual-Role Navigation", async ({ page }) => {
    test.info().annotations.push({
      type: "Requirement",
      description: "Assert Login/Signup renders, Mock Firebase Auth, verify role toggle and ME-ID copy badge.",
    })

    // 1. Assert Login/Signup renders on public landing
    await page.goto("/")
    await expect(page.locator(".public-nav")).toBeVisible()
    
    // Assert Login and Sign Up navigation actions
    const loginNavBtn = page.locator(".public-actions button:has-text('Log In')")
    const signUpNavBtn = page.locator(".public-actions button:has-text('Sign Up')")
    await expect(loginNavBtn).toBeVisible()
    await expect(signUpNavBtn).toBeVisible()

    // Click "Log In" to open Authentication stage
    await loginNavBtn.click()

    // Assert Authentication stage and forms
    const authCard = page.locator(".auth-card")
    await expect(authCard).toBeVisible()
    await expect(authCard.locator(".auth-heading h1")).toHaveText("Welcome back.")
    await expect(authCard.locator("label[for='email']")).toHaveText("Work Email")
    await expect(authCard.locator("label[for='password']")).toHaveText("Password")

    // Fill credentials and submit form
    await page.fill("#email", "alex.chen@fintech-dev.io")
    await page.fill("#password", "EnterpriseSecPass123!")
    await page.click("button.auth-continue")

    // 2. Assert transition to Dashboard and verify Topbar
    await expect(page.locator(".topbar")).toBeVisible({ timeout: 10000 })

    // Verify Center Segmented Control (Toggle Switch)
    const sponsorTab = page.locator("button.view-toggle-btn:has-text('Sponsor View')")
    const workerTab = page.locator("button.view-toggle-btn:has-text('Worker View')")
    await expect(sponsorTab).toBeVisible()
    await expect(workerTab).toBeVisible()

    // Assert initial active state is Sponsor View
    await expect(sponsorTab).toHaveClass(/active/)
    await expect(page.locator("h1:has-text('Sponsor Portfolio Surveillance')")).toBeVisible()

    // Toggle to Worker View
    await workerTab.click()
    await expect(workerTab).toHaveClass(/active/)
    await expect(page.locator("h1:has-text('Worker Dashboard')")).toBeVisible()

    // Toggle back to Sponsor View
    await sponsorTab.click()
    await expect(sponsorTab).toHaveClass(/active/)
    await expect(page.locator("h1:has-text('Sponsor Portfolio Surveillance')")).toBeVisible()

    // 3. Click Profile Pill and Assert Milestone Escrow ID (ME-ID) Badge
    const profilePill = page.locator(".profile-pill-button")
    await expect(profilePill).toBeVisible()
    await profilePill.click()

    const dropdownMenu = page.locator(".profile-dropdown-menu")
    await expect(dropdownMenu).toBeVisible()

    const meIdCard = dropdownMenu.locator(".dropdown-me-id-card")
    await expect(meIdCard).toBeVisible()
    await expect(meIdCard).toContainText("Milestone Escrow ID:")
    await expect(meIdCard).toContainText("ME-8492-X")

    // Assert copy button is functional and updates state
    const copyBtn = meIdCard.locator("button.me-id-copy-btn")
    await expect(copyBtn).toBeVisible()
    await copyBtn.click()
    await expect(copyBtn).toHaveClass(/copied/)
  })

  // =========================================================================
  // Phase 2: Sponsor Contract Custody
  // =========================================================================
  test("Phase 2: Sponsor Contract Custody", async ({ page }) => {
    test.info().annotations.push({
      type: "Requirement",
      description: "In Sponsor View, click Create New Contract, fill Section 01-03, mock PayPal v2 authorization, assert LOCKED state.",
    })

    // Setup authenticated session on Sponsor Dashboard
    await page.addInitScript(() => {
      ;(window as any).__E2E_MOCK_USER__ = {
        uid: "usr_mock_sponsor_8492",
        email: "alex.chen@fintech-dev.io",
      }
    })

    await page.goto("/?screen=dashboard&view=sponsor")
    await expect(page.locator("h1:has-text('Sponsor Portfolio Surveillance')")).toBeVisible({ timeout: 10000 })

    // Click "+ Create New Contract"
    const newContractBtn = page.locator(".sponsor-create-btn, button:has-text('+ Create New Contract')").first()
    await expect(newContractBtn).toBeVisible()
    await newContractBtn.click()

    // Assert Contract Form Container
    const formContainer = page.locator(".contract-form-container")
    await expect(formContainer).toBeVisible()

    // Section 01: Purpose Selection (Digital Freelance Code/GitHub)
    const codeCard = formContainer.locator(".purpose-card:has-text('Digital Freelance')")
    await expect(codeCard).toBeVisible()
    await codeCard.click()
    await expect(codeCard).toHaveClass(/selected/)

    // Section 02: Worker Email & ME-ID
    const workerEmailInput = formContainer.locator("#workerEmail")
    const workerMeIdInput = formContainer.locator("#workerMeId")
    await expect(workerEmailInput).toBeVisible()
    await expect(workerMeIdInput).toBeVisible()

    await workerEmailInput.fill("elena.rostova@quantum-dev.de")
    await workerMeIdInput.fill("ME-8492-X")

    // Section 03: Contract Budget & Details
    const titleInput = formContainer.locator("#contractTitle")
    const budgetInput = formContainer.locator("#contractBudget")
    await expect(titleInput).toBeVisible()
    await expect(budgetInput).toBeVisible()

    await titleInput.fill("Zero-Knowledge Proof Verifier Integration")
    await budgetInput.fill("4500")

    // Fill project deliverables brief in AI mode (active default)
    const aiPromptInput = formContainer.locator("#aiPromptInput")
    if (await aiPromptInput.isVisible()) {
      await aiPromptInput.fill(
        "Phase 1: Zero-knowledge circuits. Phase 2: Production rollup deployment. Gas benchmark < 250k."
      )
    }

    // Intercept POST /api/bounties to mock PayPal v2 authorization and backend persistence
    let paypalAuthorized = false
    await page.route("**/api/bounties", async (route) => {
      if (route.request().method() === "POST") {
        paypalAuthorized = true
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: 88,
            title: "Zero-Knowledge Proof Verifier Integration",
            amount_usd: 4500.0,
            remaining_amount_usd: 4500.0,
            status: "LOCKED",
            paypal_order_id: "ESCROW-PAYPAL-V2-AUTH-88",
            freelancer_email: "elena.rostova@quantum-dev.de",
            worker_me_id: "ME-8492-X",
            sponsor_me_id: "ME-SPON-8492",
            milestones: [
              {
                id: 188,
                title: "Milestone Phase 1: ZKP Circuits",
                amount_usd: 4500.0,
                status: "LOCKED",
              },
            ],
          }),
        })
      } else {
        // Return refreshed bounties list with newly funded contract
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              id: 88,
              title: "Zero-Knowledge Proof Verifier Integration",
              amount_usd: 4500.0,
              remaining_amount_usd: 4500.0,
              freelancer_email: "elena.rostova@quantum-dev.de",
              freelancer_phone: "ME-8492-X",
              worker_me_id: "ME-8492-X",
              sponsor_me_id: "ME-SPON-8492",
              status: "LOCKED",
              risk_score: "LOW",
              risk_percentage: 14,
              milestones: [
                {
                  id: 188,
                  bounty_id: 88,
                  description: "Milestone Phase 1: ZKP Circuits",
                  amount_usd: 4500.0,
                  deadline: "2026-12-15",
                  percentage: 100,
                  status: "LOCKED",
                },
              ],
            },
          ]),
        })
      }
    })

    // Click "Fund Escrow Contract"
    const fundBtn = formContainer.locator("button.fund-escrow-btn")
    await expect(fundBtn).toBeVisible()
    await fundBtn.click()

    // Assert redirection to Sponsor Portfolio Hub
    await expect(page.locator("h1:has-text('Sponsor Portfolio Surveillance')")).toBeVisible({ timeout: 10000 })
    expect(paypalAuthorized).toBe(true)

    // Assert contract appears in table with "LOCKED" status badge
    const contractRow = page.locator("tr:has-text('Zero-Knowledge Proof Verifier Integration')")
    await expect(contractRow).toBeVisible()
    await expect(contractRow.locator(".status-badge")).toContainText("LOCKED")
  })

  // =========================================================================
  // Phase 3: Worker Submission & AI Audit Trigger
  // =========================================================================
  test("Phase 3: Worker Submission & AI Audit Trigger", async ({ page }) => {
    test.info().annotations.push({
      type: "Requirement",
      description: "In Worker View, assert assigned contract, open Submit Proof drawer, upload git/OCR file, intercept Gemini 2.5 Pro audit.",
    })

    await page.addInitScript(() => {
      ;(window as any).__E2E_MOCK_USER__ = {
        uid: "usr_mock_worker_8492",
        email: "elena.rostova@quantum-dev.de",
      }
    })

    // Load Worker Dashboard
    await page.goto("/?screen=dashboard&view=worker")
    await expect(page.locator("h1:has-text('Worker Dashboard')")).toBeVisible({ timeout: 10000 })

    // Assert assigned contract row appears in table
    const workerRow = page.locator("table tbody tr:has-text('Autonomous Algorithmic Liquidity Router')")
    await expect(workerRow).toBeVisible()

    // Click "Submit Proof" to open slide-out drawer
    const submitProofBtn = workerRow.locator("button.outline-proof-btn:has-text('Submit Proof')")
    await expect(submitProofBtn).toBeVisible()
    await submitProofBtn.click()

    // Assert slide-out drawer is open
    const drawer = page.locator(".drawer-panel")
    await expect(drawer).toBeVisible()
    await expect(drawer.locator("h2")).toContainText("Submit Deliverable Proof")

    // Fill GitHub URL input
    const gitInput = drawer.locator("#githubUrlInput")
    await expect(gitInput).toBeVisible()
    await gitInput.fill("https://github.com/apex-fintech/settlement-core/pull/42")

    // Mock file upload in the OCR dropzone
    const fileInput = drawer.locator("input[type='file']")
    await fileInput.setInputFiles({
      name: "zkp_benchmark_audit_receipt.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 Mock ZKP Benchmark Verification Proof & Metadata"),
    })

    // Assert file selected card renders
    await expect(drawer.locator(".file-selected-card")).toBeVisible()
    await expect(drawer.locator(".file-name-text")).toHaveText("zkp_benchmark_audit_receipt.pdf")

    // Intercept POST /api/submit-work to mock Gemini 2.5 Pro multimodal code evaluation
    let geminiAuditTriggered = false
    await page.route("**/submit-work", async (route) => {
      geminiAuditTriggered = true
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          passed: true,
          milestone_id: 91,
          milestone_status: "COMPLETED",
          milestone_amount_paid: 4500.0,
          remaining_amount_usd: 0.0,
          feedback: "Google Gemini 2.5 Pro AST Code QA PASSED: 100% acceptance criteria satisfied. Verified against halo2 prover benchmarks.",
          extracted_data: {
            ast_syntactic_nodes: 3410,
            cyclomatic_complexity: 3.8,
            exif_timestamp_valid: true,
          },
        }),
      })
    })

    // Click "Submit to AI Auditor"
    const submitToAuditorBtn = drawer.locator("button.submit-auditor-btn")
    await expect(submitToAuditorBtn).toBeVisible()
    await submitToAuditorBtn.click()

    // Assert AI audit submission state feedback
    await expect(drawer.locator("button.submit-auditor-btn")).toContainText(/Submitted for Autonomous Release|Auditing Deliverables/)
    expect(geminiAuditTriggered).toBe(true)
  })

  // =========================================================================
  // Phase 4: Autonomous Arbitration & Scope Negotiation
  // =========================================================================
  test("Phase 4: Autonomous Arbitration & Scope Negotiation", async ({ page }) => {
    test.info().annotations.push({
      type: "Requirement",
      description: "Force REJECTED AI verdict, assert Split-Screen Modal, submit appeal, assert compromise card, ratify terms.",
    })

    await page.addInitScript(() => {
      ;(window as any).__E2E_MOCK_USER__ = {
        uid: "usr_mock_worker_8492",
        email: "elena.rostova@quantum-dev.de",
      }
    })

    await page.goto("/?screen=dashboard&view=worker")
    await expect(page.locator("h1:has-text('Worker Dashboard')")).toBeVisible({ timeout: 10000 })

    // Open the Split-Screen Audit & Negotiation Hub modal via "View AI Audit"
    const viewAuditBtn = page.locator("button:has-text('View AI Audit')").first()
    await expect(viewAuditBtn).toBeVisible()
    await viewAuditBtn.click()

    // Assert Centered Split-Screen Modal Container
    const modalContainer = page.locator(".audit-modal-container")
    const modalGrid = page.locator(".audit-modal-grid")
    await expect(modalContainer).toBeVisible()
    await expect(modalGrid).toBeVisible()

    // 1. Left Column (AI Auditor Verdict) Assertions
    const leftCol = modalGrid.locator(".audit-column-left")
    await expect(leftCol).toBeVisible()
    await expect(leftCol.locator("h2")).toHaveText("Autonomous Audit Dossier")

    // Assert large status badge is REJECTED
    const verdictBadge = leftCol.locator(".large-verdict-badge")
    await expect(verdictBadge).toBeVisible()
    await expect(verdictBadge).toContainText("REJECTED - Missing Requirements")

    // Assert clean checklist of acceptance criteria with [X] marks on failed items
    const checklist = leftCol.locator(".criteria-checklist")
    await expect(checklist).toBeVisible()
    const failedIcons = checklist.locator(".criteria-checklist-item .icon-fail")
    expect(await failedIcons.count()).toBeGreaterThanOrEqual(2)

    // Assert extracted receipt / GitHub telemetry box
    const telemetryBox = leftCol.locator(".extracted-summary-box")
    await expect(telemetryBox).toBeVisible()
    await expect(telemetryBox).toContainText("Extracted Receipt / GitHub Telemetry")

    // 2. Right Column (AI Mediator & Scope Negotiation) Assertions
    const rightCol = modalGrid.locator(".audit-column-right")
    await expect(rightCol).toBeVisible()
    await expect(rightCol.locator("h2")).toHaveText("Mediator AI: Scope Negotiation")

    // Fill worker appeal / counter-offer in textarea
    const appealTextarea = rightCol.locator("#appeal-textarea")
    await expect(appealTextarea).toBeVisible()
    await appealTextarea.fill(
      "The 142ms latency was caused by uncached Redis cold-starts. Production warm cache measures 62ms. Propose ratifying 87.5% milestone disbursement."
    )

    // Mock Mediator AI appeal response
    await page.route("**/appeal", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "compromise_proposed",
          compromise_budget: 3937.5,
          deferred_budget: 562.5,
          extended_days: 15,
        }),
      })
    })

    // Click "Submit Appeal" button
    const submitAppealBtn = rightCol.locator("button.modal-outline-appeal-btn:has-text('Submit Appeal')")
    await expect(submitAppealBtn).toBeVisible()
    await submitAppealBtn.click()

    // Assert distinct "AI Proposed Compromise" card renders
    const compromiseCard = rightCol.locator(".ai-compromise-card")
    await expect(compromiseCard).toBeVisible()
    await expect(compromiseCard.locator(".compromise-badge")).toContainText("AI Proposed Compromise")

    // Assert side-by-side comparison boxes
    const originalTermsBox = compromiseCard.locator(".comparison-box.original-terms")
    const proposedTermsBox = compromiseCard.locator(".comparison-box.proposed-compromise")
    await expect(originalTermsBox).toBeVisible()
    await expect(proposedTermsBox).toBeVisible()
    await expect(originalTermsBox.locator(".comparison-box-title")).toHaveText("Original Deadline & Budget")
    await expect(proposedTermsBox.locator(".comparison-box-title")).toHaveText("AI Proposed Compromise")

    // Click "Ratify AI Compromise" button
    const ratifyBtn = rightCol.locator("button.modal-ratify-compromise-btn:has-text('Ratify AI Compromise')")
    await expect(ratifyBtn).toBeVisible()
    await ratifyBtn.click()

    // Assert contract terms update notification / ratification banner
    const notificationBanner = page.locator(".audit-modal-notification")
    await expect(notificationBanner).toBeVisible({ timeout: 10000 })
    await expect(notificationBanner).toContainText("Compromise Ratified!")
  })

  // =========================================================================
  // Phase 5: High-Value Admin Multi-Sig (>$5k)
  // =========================================================================
  test("Phase 5: High-Value Admin Multi-Sig (>$5k)", async ({ page }) => {
    test.info().annotations.push({
      type: "Requirement",
      description: "Mock $10k contract in PENDING_ADMIN, navigate to /admin, verify 2FA TOTP gate, co-sign & execute payout.",
    })

    // Mock high-value contract ($10,000) entering PENDING_ADMIN queue
    await page.route("**/api/bounties", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: 99,
            title: "Global Interbank Liquidity Mesh Protocol",
            amount_usd: 10000.0,
            remaining_amount_usd: 10000.0,
            freelancer_email: "marcus.vance@institutional-infra.ch",
            freelancer_phone: "ME-WK-9942",
            worker_me_id: "ME-WK-9942",
            sponsor_me_id: "ME-SPON-8492",
            status: "PENDING_ADMIN",
            requires_human_approval: true,
            is_high_value: true,
            risk_score: "LOW",
            milestones: [
              {
                id: 199,
                bounty_id: 99,
                description: "Sub-5ms Atomic Arbitrage Settlement Engine",
                amount_usd: 10000.0,
                status: "PENDING_ADMIN",
                requires_human_approval: true,
              },
            ],
          },
        ]),
      })
    })

    // 1. Navigate to /admin and assert standard login is hidden, requiring 2FA TOTP
    await page.goto("/admin")

    // Assert restricted Admin 2FA Gateway is rendered
    await expect(page.locator(".auth-heading h1")).toHaveText("Secure Compliance Admin Login")
    // Assert standard social/signup tabs are hidden
    await expect(page.locator(".auth-segment")).not.toBeVisible()

    // Assert 2FA TOTP input is present and mandatory
    const totpInput = page.locator("#twoFactorCode")
    await expect(totpInput).toBeVisible()
    await expect(page.locator("label[for='twoFactorCode']")).toContainText("2FA Authenticator Code")

    // Fill Admin credentials and 6-digit TOTP code
    await page.fill("#email", "admin@compliance.escrow.io")
    await page.fill("#password", "SOC2-Admin-Pass-9921!")
    await totpInput.fill("849201")

    // Submit Admin Authentication
    const authAdminBtn = page.locator("button.auth-admin-button")
    await expect(authAdminBtn).toBeVisible()
    await authAdminBtn.click()

    // 2. In Admin Dashboard, assert high-value contract is in the queue
    await expect(page.locator("h1:has-text('High-Value Transaction Queue')")).toBeVisible({ timeout: 10000 })
    
    const adminTable = page.locator(".admin-full-table")
    await expect(adminTable).toBeVisible()

    const highValueRow = adminTable.locator("tr.admin-table-row:has-text('Global Interbank Liquidity Mesh Protocol')")
    await expect(highValueRow).toBeVisible()
    await expect(highValueRow.locator(".admin-amount-cell strong")).toHaveText("$10,000.00")
    await expect(highValueRow.locator(".admin-audit-pill.passed")).toContainText("AI AUDIT: PASSED")

    // 3. Click "Review & Approve" and assert co-signer modal appears
    const reviewApproveBtn = highValueRow.locator("button.admin-review-approve-btn:has-text('Review & Approve')")
    await expect(reviewApproveBtn).toBeVisible()
    await reviewApproveBtn.click()

    // Co-Signer Modal Assertion
    const coSignModal = page.locator(".admin-modal-card")
    await expect(coSignModal).toBeVisible()
    await expect(coSignModal.locator(".admin-modal-eyebrow")).toHaveText("COMPLIANCE MULTI-SIG AUTHORIZATION")
    await expect(coSignModal.locator(".strict-confirmation-banner")).toContainText("$10,000.00")
    await expect(coSignModal.locator(".strict-confirmation-banner")).toContainText("ME-WK-9942")

    // 4. Intercept Admin Approval & PayPal Payouts Webhook
    let payoutExecuted = false
    await page.route("**/api/admin/bounties/99/milestones/199/approve", async (route) => {
      payoutExecuted = true
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "success",
          milestone_id: 199,
          payout_batch_id: "PAYPAL-PAYOUT-BATCH-778899",
          transaction_status: "COMPLETED",
          disbursed_usd: 10000.0,
        }),
      })
    })

    // Click "Execute Payout"
    const executePayoutBtn = coSignModal.locator("button.execute-payout-btn:has-text('Execute Payout')")
    await expect(executePayoutBtn).toBeVisible()
    await executePayoutBtn.click()

    // Assert payout execution completed and toast banner confirmed
    expect(payoutExecuted).toBe(true)
    const toast = page.locator(".admin-toast-banner")
    await expect(toast).toBeVisible({ timeout: 5000 })
    await expect(toast).toContainText("Co-Signed & Released via PayPal Multi-Sig!")
  })
})
