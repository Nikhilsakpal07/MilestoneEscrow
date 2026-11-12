"use client"

import React, { useEffect, useRef, useState } from "react"
import { gsap } from "gsap"
import { useAuth } from "../AuthContext"
import { getUserProfile, updateUserProfile } from "../api"

export interface AccountSettingsModalProps {
  isOpen: boolean
  onClose: () => void
  fallbackEmail?: string
  fallbackMeId?: string
}

export default function AccountSettingsModal({
  isOpen,
  onClose,
  fallbackEmail = "",
  fallbackMeId = "",
}: AccountSettingsModalProps) {
  const { user: authUser, firebaseUser, setUser } = useAuth()
  const [paypalEmail, setPaypalEmail] = useState("")
  const [loadingProfile, setLoadingProfile] = useState(false)
  const [saving, setSaving] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  const loginEmail = authUser?.email || fallbackEmail
  const displayMeId = authUser?.me_id || fallbackMeId

  useEffect(() => {
    if (!isOpen) return
    setErrorMsg(null)
    setSuccessMsg(null)

    if (cardRef.current) {
      gsap.fromTo(
        cardRef.current,
        { opacity: 0, y: 14, scale: 0.98 },
        { opacity: 1, y: 0, scale: 1, duration: 0.28, ease: "power2.out" }
      )
    }

    // Seed immediately from context while refreshing from GET /api/users/me
    const initialPayPal = authUser?.paypal_email || loginEmail
    setPaypalEmail(initialPayPal)

    let active = true
    const loadFromBackend = async () => {
      if (!authUser?.firebase_uid && !loginEmail) return
      setLoadingProfile(true)
      try {
        let token = authUser?.idToken
        if (!token && firebaseUser) {
          token = await firebaseUser.getIdToken().catch(() => undefined)
        }
        const res = await getUserProfile({
          token,
          firebaseUid: authUser?.firebase_uid,
          email: loginEmail,
        })
        if (active && res?.user) {
          setPaypalEmail(res.user.paypal_email || res.user.email || initialPayPal)
          setUser({
            ...(authUser || {
              firebase_uid: res.user.firebase_uid,
              email: res.user.email,
              me_id: res.user.me_id,
            }),
            ...res.user,
            idToken: token,
          })
        }
      } catch (err) {
        console.warn("[AccountSettings] Using cached profile:", err)
      } finally {
        if (active) setLoadingProfile(false)
      }
    }

    loadFromBackend()
    return () => {
      active = false
    }
  }, [isOpen])

  if (!isOpen) return null

  const handleSaveChanges = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMsg(null)
    setSuccessMsg(null)

    const cleanPayPalEmail = paypalEmail.trim().toLowerCase()
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(cleanPayPalEmail)) {
      setErrorMsg("Please enter a valid PayPal email address.")
      return
    }

    setSaving(true)
    try {
      let token = authUser?.idToken
      if (!token && firebaseUser) {
        token = await firebaseUser.getIdToken().catch(() => undefined)
      }

      const res = await updateUserProfile(
        {
          firebase_uid: authUser?.firebase_uid,
          email: loginEmail,
          paypal_email: cleanPayPalEmail,
        },
        token
      )

      if (res?.user && authUser) {
        setUser({
          ...authUser,
          ...res.user,
          paypal_email: res.user.paypal_email || cleanPayPalEmail,
        })
      } else if (authUser) {
        setUser({ ...authUser, paypal_email: cleanPayPalEmail })
      }

      setPaypalEmail(cleanPayPalEmail)
      setSuccessMsg("PayPal Payout Address saved. Released milestone payouts will route here.")
    } catch (err: any) {
      setErrorMsg(err?.message || "Could not update PayPal Payout Address.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="account-settings-title"
    >
      <div
        ref={cardRef}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-xl border border-zinc-800 bg-[#0a0a0a] p-6 text-white shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-zinc-800/80 pb-4">
          <div>
            <p className="text-[11px] font-mono uppercase tracking-widest text-emerald-400">
              Identity &amp; Payout Routing
            </p>
            <h2 id="account-settings-title" className="mt-1 text-lg font-semibold text-white">
              Account Settings
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close Account Settings"
            className="rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 py-1 text-xs text-zinc-400 hover:border-zinc-700 hover:text-white transition-colors"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSaveChanges} className="mt-5 space-y-5">
          {/* Immutable Identity Summary */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 rounded-lg border border-zinc-800/80 bg-zinc-950/70 p-3.5">
            <div>
              <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">
                Login Email
              </div>
              <div className="mt-1 truncate text-xs font-medium text-zinc-200" title={loginEmail}>
                {loginEmail || "Not signed in"}
              </div>
            </div>
            <div>
              <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">
                Immutable ME-ID
              </div>
              <div className="mt-1 font-mono text-xs font-semibold text-emerald-400">
                {displayMeId || "ME-UNASSIGNED"}
              </div>
            </div>
          </div>

          {/* Dedicated PayPal Payout Address */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label
                htmlFor="paypalPayoutEmail"
                className="text-xs font-semibold uppercase tracking-wider text-zinc-200"
              >
                PayPal Payout Address
              </label>
              {loadingProfile && (
                <span className="text-[11px] font-mono text-zinc-500">Syncing...</span>
              )}
            </div>
            <input
              id="paypalPayoutEmail"
              type="email"
              required
              value={paypalEmail}
              onChange={(e) => setPaypalEmail(e.target.value)}
              placeholder="payouts@your-paypal-account.com"
              disabled={saving}
              className="w-full rounded-lg border border-zinc-800 bg-black px-3.5 py-2.5 text-sm text-white placeholder-zinc-600 focus:border-emerald-500 focus:outline-none"
            />
            <p className="text-xs text-zinc-400 leading-relaxed">
              Funds released from completed escrow contracts will be sent directly to this PayPal account.
            </p>
          </div>

          {errorMsg && (
            <div
              role="alert"
              className="rounded-lg border border-red-500/40 bg-red-500/10 px-3.5 py-2.5 text-xs text-red-400"
            >
              {errorMsg}
            </div>
          )}

          {successMsg && (
            <div
              role="status"
              className="rounded-lg border border-green-500/50 bg-green-500/10 px-3.5 py-2.5 text-xs text-green-400"
            >
              {successMsg}
            </div>
          )}

          <div className="flex items-center justify-end gap-3 border-t border-zinc-800/80 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-zinc-800 bg-transparent px-4 py-2 text-xs font-medium text-zinc-400 hover:border-zinc-700 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-xs font-semibold text-black hover:bg-emerald-400 disabled:opacity-50 transition-colors"
            >
              {saving ? "Saving Changes..." : "Save Changes"}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

