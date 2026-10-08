# Planly Chrome extension

Sends the course pages **you choose** to your Planly plan. It works only on sites you
switch on, and Chrome itself enforces that: until you press **On** for a site, the
extension has no access to it at all.

It uses the login already in your Chrome, so there's nothing to set up per site and no
course passwords go anywhere. It only reads what the page shows (titles, lengths,
deadlines, ticks). It never submits, buys or downloads anything.

## Install (2 minutes, once)
1. Chrome → `chrome://extensions` → switch on **Developer mode** (top right).
2. **Load unpacked** → pick this `extension` folder.
3. Pin it: puzzle-piece icon → pin **Planly**.
4. Click the icon → **Settings** → paste the **Supabase anon key**
   (Supabase → Project Settings → API → `anon` / publishable key. It's public by design.
   Never the `service_role` key.) → Save.
5. Sign in with your Planly email and password (same as the web app).

## Use
1. Open the course page that **lists** the lectures / problems (IITM course page, Udemy
   curriculum, Striver sheet, LeetCode study plan...).
2. Click Planly → press **On** for this site (Chrome asks once).
3. Pick the goal → **Sync this page**. You can close the popup; it keeps going.
4. Leave "Re-sync when I open this page" ticked: each time you open that page again
   (at most every 6 hours) it re-reads it quietly, so new lectures, your ticks and
   changed deadlines reach the plan on their own. The icon shows ✓ or ! on the tab.

The courses then show on the goal page in Planly, under **Your courses**.

## Verification (watch time)
On sites you switched **On**, Planly records which parts of each video actually play: only while
the tab is visible, not ads, not above 2x, and skipped parts don't count. A small Planly bar
(bottom right) always shows when it's recording, which of today's tasks the video belongs to,
how much you've watched, and a checkbox. At 80% watched the task ticks itself.

Lectures embedded from YouTube (IITM) need **youtube.com switched On too**.

Every tick is checked when the day ends. Proof you didn't do it (you opened the video but under
half played / the site still shows the problem unsolved) = no credit, +25% time owed, trust −15.
No evidence at all (watched on your phone) = self-reported, never punished.

Switch a site **Off** any time: Chrome removes the access and Planly forgets its pages.

## Updating the extension
After replacing this folder with a newer version: `chrome://extensions` → Planly → ↻ reload.


## What gets recorded (since 0.2.0)

Only lectures that are in one of your synced courses or playlists. With YouTube switched on, a
music video or anything else outside your courses is ignored: nothing is recorded or sent, and the
Planly bar doesn't appear. To track a new playlist, sync it first (popup → Sync this page); its
videos are tracked within a few minutes.
