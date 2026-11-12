"use client"

import React, { useEffect, useRef, useState } from "react"
import { PayPalButtons, PayPalScriptProvider } from "@paypal/react-paypal-js"
import { gsap } from "gsap"
import { createMilestonePayPalOrder, captureMilestonePayPalOrder } from "../api"

export type FundingStatus = "PENDING" | "PROCESSING" | "FUNDED"

export interface PayPalFundButtonProps {
  milestoneId: number | string
  amount: number
  onEnsureMilestoneId?: () => Promise<number | string>
  onFunded?: (orderId: string) => void
  disabled?: boolean
}

export default function PayPalFundButton({
  milestoneId,
  amount,
  onEnsureMilestoneId,
  onFunded,
  disabled = false,
}: PayPalFundButtonProps) {
  const [fundingStatus, setFundingStatus] = useState<FundingStatus>("PENDING")
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [capturedOrderId, setCapturedOrderId] = useState<string | null>(null)
  const activeMilestoneIdRef = useRef<number | string>(milestoneId)
  const successBannerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    activeMilestoneIdRef.current = milestoneId
  }, [milestoneId])

  useEffect(() => {
    if (fundingStatus === "FUNDED" && successBannerRef.current) {
      gsap.fromTo(
        successBannerRef.current,
        { opacity: 0, y: 10, scale: 0.98 },
        { opacity: 1, y: 0, scale: 1, duration: 0.4, ease: "power2.out" }
      )
    }
  }, [fundingStatus])

  const paypalClientId = process.env.NEXT_PUBLIC_PAYPAL_CLIENT_ID || "sb"

  if (fundingStatus === "FUNDED") {
    return (
      <div
        ref={successBannerRef}
        role="status"
        className="w-full rounded-lg border border-green-500 bg-green-500/10 px-4 py-3 text-green-400 flex items-center justify-between gap-3"
      >
        <div className="flex items-center gap-2.5">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            width={18}
            height={18}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M20 6 9 17l-5-5" />
          </svg>
          <div className="text-sm font-medium">
            Escrow Deposit Captured &amp; Locked (${Number(amount || 0).toLocaleString("en-US", { minimumFractionDigits: 2 })} USD)
          </div>
        </div>
        {capturedOrderId && (
          <span className="text-xs font-mono opacity-80">
            Order: {capturedOrderId}
          </span>
        )}
      </div>
    )
  }

  return (
    <div className="w-full space-y-2.5">
      {errorMsg && (
        <div
          role="alert"
          className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400"
        >
          {errorMsg}
        </div>
      )}

      {fundingStatus === "PROCESSING" && (
        <div className="rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 text-xs text-zinc-300 font-mono">
          Capturing PayPal escrow deposit &amp; locking milestone funds...
        </div>
      )}

      <PayPalScriptProvider
        options={{
          clientId: paypalClientId,
          currency: "USD",
          intent: "capture",
        }}
      >
        <PayPalButtons
          disabled={disabled || fundingStatus === "PROCESSING" || !amount || Number(amount) <= 0}
          forceReRender={[milestoneId, amount, disabled]}
          style={{
            layout: "vertical",
            color: "black",
            shape: "rect",
          }}
          createOrder={async () => {
            setErrorMsg(null)
            const numericAmount = Number(amount)
            if (!numericAmount || numericAmount <= 0) {
              const msg = "Please enter a valid positive Total Escrow Budget before funding."
              setErrorMsg(msg)
              throw new Error(msg)
            }

            let targetMilestoneId = activeMilestoneIdRef.current
            if (onEnsureMilestoneId) {
              try {
                targetMilestoneId = await onEnsureMilestoneId()
                activeMilestoneIdRef.current = targetMilestoneId
              } catch (err: any) {
                const msg = err?.message || "Contract validation failed."
                setErrorMsg(msg)
                throw err
              }
            }

            const res = await createMilestonePayPalOrder(targetMilestoneId, numericAmount)
            const orderId = res?.order_id || res?.id
            if (!orderId) {
              throw new Error("Backend did not return a PayPal order_id.")
            }
            return orderId
          }}
          onApprove={async (data) => {
            try {
              setFundingStatus("PROCESSING")
              setErrorMsg(null)
              const targetMilestoneId = activeMilestoneIdRef.current
              await captureMilestonePayPalOrder(targetMilestoneId, data.orderID)
              setCapturedOrderId(data.orderID)
              setFundingStatus("FUNDED")
              if (onFunded) {
                onFunded(data.orderID)
              }
            } catch (err: any) {
              setFundingStatus("PENDING")
              setErrorMsg(err?.message || "Failed to capture PayPal escrow payment.")
            }
          }}
          onCancel={() => {
            setFundingStatus("PENDING")
          }}
          onError={(err) => {
            console.error("[PayPalFundButton] PayPal checkout error:", err)
            setFundingStatus("PENDING")
            setErrorMsg("PayPal checkout encountered an error. Verify your contract details and try again.")
          }}
        />
      </PayPalScriptProvider>
    </div>
  )
}

