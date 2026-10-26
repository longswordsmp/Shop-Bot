# Reviews (`reviews`)

Specification: section 3 (CATALOG / `#reviews`): "Verified reviews submitted through the bot's review workflow. Each
review embed can show the review rating, approved comment, verification status, and a reference to the relevant
completed transaction or record. Only eligible users can submit reviews. Enforce the configured review limits and
prevent duplicate submissions for the same qualifying transaction. Do not display aggregate ratings unless explicitly
enabled in configuration." Also section 6 (approval / rejection notifications) and section 2 (the Owner and staff
approve or reject submissions). The owner later asked for `/review` for every member (stars, a description and a
photo, never their name) and an intro at the top of #reviews after the server was rebuilt.

## What it does

1. **Review request.** When an order becomes `fulfilled` (checkout calls `schedule_review_prompt`), the task
   `reviews.prompt` runs `reviews.prompt_delay_minutes` later and sends the customer a request with a rating menu and
   a decline button, by direct message and/or in the order channel (`reviews.prompt_via`). Only the order's customer
   can answer it, only while the order is still fulfilled. Its text mentions that **Write a review** in #reviews
   lets them add a photo.
2. **Rating and comment.** Picking a rating opens the comment form (`reviews.allow_comment`, optional or required,
   at most `reviews.comment_max_length` characters) or finishes the review right away. A comment the published review
   (or the staff approval message) could not show within Discord's limits is refused, so it can be shortened.
3. **Write a review and `/review` (every member).** Every channel a member sees is read-only for them (#reviews
   too), and Discord does not offer slash commands where a member cannot send messages, so the intro panel of
   #reviews has a **Write a review** button: it answers privately with a short text and a stars menu (the rating
   labels, highest first); picking stars opens the private review form: the description (required, at most
   `reviews.comment_max_length` characters) and an optional **photo upload** (Discord's file upload field in forms,
   one file). `/review` does the same where a member can type (an open order or support ticket): `stars` (required;
   the choices are the rating labels with a row of stars, e.g. "5 stars - Excellent ★★★★★") and `photo` (an optional
   picture; the customer texts call it the photo option); its form asks for the photo only when the option was left
   out. The button checks first whether the member can write a review now and answers why not instead of the menu;
   the stars are checked again when picked (a menu opened earlier then shows why instead). Both ways write the same
   review:
   - when the member has a delivered (fulfilled) order without a finished review, the newest one is reviewed: a
     **verified** review with the verification marker, the same limits and duplicate rules as a review request (one
     review per order). An open review request of that order is finished by it (in the order channel it shows the
     thank-you text; the rating menu of a request sent by direct message answers "already reviewed"), and a request
     that was still scheduled is cancelled. A request the customer declined, or a review staff removed, closes its
     order for reviews (both are `declined`);
   - otherwise it is an **unverified** review (no order), when `reviews.allow_unverified` is on, so returning
     customers can review again after the server was rebuilt. It **always waits for staff approval** (also with
     `reviews.require_approval` off), and a member writes at most one every `reviews.unverified_cooldown_days` days
     (0 = only one, ever). Every unverified review counts, whatever became of it (also rejected or removed ones), so
     a rejected review cannot simply be sent again. Off: such members are told that reviews are for delivered orders.
   - A review with a photo also **waits for staff approval** while `reviews.photo_requires_approval` is on (shipped),
     a verified one too, so no picture appears in #reviews unchecked.
   - The member is told privately whether the review is published or waits for our team's check
     (`reviews.written*`). Refusals are private too: the cooldown (with the day they can write again), the limit
     (cooldown 0), no delivered order (unverified reviews off), a photo that cannot be used, a description Discord
     could not show, a form that expired (6 hours) or was already sent. A photo refused after the form was sent
     answers with the member's description under `reviews.image_problem.text_field`, so the text is not lost. Every
     reply is built before anything is written. The form is kept in `kv_state` (`reviews.form:<token>`), so it still
     works after a restart.
4. **The photo.** PNG, JPG, GIF or WebP, at most the per-message file limit (capped by the server's upload limit)
   minus the largest banner a message with the photo carries (the reviews banner, or one of the approval message's
   banners), as for announcements with an image (the limit is shown rounded down to whole MB). The declared type
   (one of those four, or else the file name's ending) and the size are checked before the form opens for the photo
   option of `/review`, so a HEIC or SVG picture is refused before anything is typed; a photo uploaded in the form
   (Discord lets members pick any file there) is checked the same way when the form is sent. The bytes are downloaded
   and checked (format from the file's first bytes) when the form is sent. Because approval can
   take days and Discord's attachment links expire, the picture is stored in the database (`review_images`, in the
   review's transaction) and attached from there to the approval message and the published review
   (`review-<review_id>.<ext>`, never the uploaded file's name). The photo closes the card and the banner shows above
   it. Once the review is published, rejected or removed, nothing posts it again, so the bytes are released
   (`data` cleared, `released_at` set; startup and `/setup` release any that were left), but only after the staff
   decision's follow-up (`reviews.decided`) has run, so the approval message shows the decision with the photo also
   after a restart. A later edit of the approval message then shows the card without the photo (removing a review
   takes its photo off the approval message at once). Released bytes are overwritten in the database file
   (`PRAGMA secure_delete`); a backup taken before the release still holds them. The stored picture is the file
   without its metadata (`shopbot/ui/image_metadata.py`, pure Python): JPEG loses its comments and every application
   block except the three a decoder needs (JFIF, the colour profile, Adobe's colour transform), so Exif, XMP, IPTC,
   Content Credentials and a camera's GPS track go (GPS position, camera, owner; only the orientation is kept, so the
   photo stays upright), and anything after the image; PNG loses its text, Exif and Content Credentials chunks, WebP
   its EXIF, XMP and Content Credentials chunks; GIF is kept as it is. The image data itself is not changed. The
   bytes are stripped again whenever they are attached, so a photo stored before stripping existed (or before it
   removed as much) is posted without its metadata too. Stripping runs in a worker thread, never in the bot's event
   loop.
5. **Anonymous publication.** A published review never shows the member's name, mention, avatar or anything that
   identifies them: the title is the rating (`reviews.published.title`, shipped `{stars} {rating}/{scale}`, e.g.
   "★★★★★ 5/5"), then the comment or description exactly as written (escaped, it never pings), the optional rating
   field, the **verification marker** of a review of a delivered order (`reviews.published.verified_field` +
   `.verified_value`, shipped "Verified purchase" / "Order #12": the order number does not identify anyone) and the
   photo. An unverified review shows no marker, so it never claims a purchase. The published keys offer only
   `{stars}`, `{rating}`, `{scale}` (and `{order_number}` in the marker): a title or marker text that uses
   `{user_name}` is refused by the content check and keeps reviews hidden until it is fixed; the other public texts
   (the rating field and rating displays) take no placeholders, so `{user_name}` there would be shown as written,
   never filled in. A failed publication is retried (task
   `reviews.publish`) until it succeeds; a crash between the post and its record never posts a second copy (claim +
   adoption of the unrecorded post). No aggregate rating is shown anywhere.
6. **Staff approval** (`reviews.require_approval = true` for every review; always for unverified reviews; for reviews
   with a photo while `reviews.photo_requires_approval` is on). The
   submitted review is *not* published. Instead:
   - the customer sees `reviews.thanks_pending.description` (review request) or `reviews.written_pending.description`
     (`/review` or Write a review);
   - the review becomes `pending_approval` and an **approval message** is posted in `reviews.approval_channel`
     (a staff-only channel, shipped `staff_chat`): title (review number, the member's name and the rating), the
     comment, rating, order number ("#12", or `reviews.approval.no_order` for an unverified review), the customer
     (mention),
     status, the photo, and the **Approve** / **Reject** buttons. Staff see who wrote it (staff-only channel). Posting
     is retried (task `reviews.approval`) until it works and never duplicated (same claim/adoption protocol as the
     publication);
   - **Approve** (staff only, checked by the router and again in the handler) changes `pending_approval -> approved`
     exactly once, the message shows "approved by @staff" without buttons, and the review is published in #reviews.
     Approving a verified review is refused when the order is no longer fulfilled (refunded/cancelled meanwhile):
     such a review cannot be a verified review, so staff reject it instead;
   - **Reject** opens a staff-only form (operator text) with an optional reason (max 500 characters). The review
     becomes `rejected` exactly once and is never published; the message shows "rejected by @staff" and the reason
     (when `reviews.approval.reason_field` is set);
   - the customer gets an optional private **notice** for both outcomes (`reviews.notice.approved.*` once the review
     is published, with `{review_link}`; `reviews.notice.rejected.*`, with the reason under
     `reviews.notice.rejected.reason_field` when that heading is set). Notices go by direct message when
     `features.dm_notifications` is on, else (or when DMs are closed) in the order channel with a mention
     (`notifications.fallback_to_channel`; an unverified review has no order channel). Each review gets at most one
     notice. The notices name no order number (a review may have none);
   - every decision is audited (`reviews.approved` / `reviews.rejected`, actor, order number, whether it is verified,
     rating, whether a reason was given - not the reason text) and the follow-ups (message update, publication or
     notice, audit) run again from the durable task `reviews.decided` if the process stopped right after the decision;
   - a second click, a click by a second staff member at the same time, or a late `/reviews approve|reject` changes
     nothing and is answered privately with the review's current state (the clicked message is refreshed).
7. **The #reviews intro panel.** `/setup` keeps one panel at the top of #reviews (`upsert_message`, record
   `reviews:intro`; the reviews banner closes it): the server was rebuilt and the older reviews did not come with us,
   they will come back over time; a warm welcome to new customers; how to leave a review with the **Write a review**
   button below it (stars, a description, an optional photo; the name is never shown), and that `/review` works in an
   open ticket. Its last line is chosen from the settings so it is
   always true: every review is checked (`require_approval` on), reviews with a photo and reviews without a delivered
   order are (`photo_requires_approval` and `allow_unverified` on, as shipped), only one of those kinds is, or no
   line (`reviews.intro.checked_all|checked_photos_unverified|checked_unverified|checked_photos`). It is edited in
   place when its text changes and re-posted when deleted. The panel system does not pin messages, so it is not
   pinned. The first `/setup` after this change posts it at the bottom of the channel: in a #reviews that already
   shows reviews it comes after them (delete those or the panel and run `/setup` again to have it first). While
   reviews are not ready the panel is withdrawn like other panels; the next `/setup` posts it again at the bottom,
   below the reviews published meanwhile.
8. **Staff tools.** `/reviews list|remove|resend|approve|reject` (see Commands).

Turning approval off again does not publish reviews that are still waiting: they keep their approval message and can
still be approved or rejected (buttons or commands). New submissions are published right away again (unverified
reviews, and reviews with a photo while `photo_requires_approval` is on, still wait).

## Settings (`[reviews]` in `config/settings.toml`)

| Setting | Shipped | Meaning |
|---|---|---|
| `channel` | `"reviews"` | Where published reviews (and the intro panel) are posted (required). |
| `prompt_delay_minutes` | `10080` | **Business decision**: minutes after fulfillment before the request (0 = immediately). |
| `rating_scale` | `5` | **Business decision**: number of rating options (1-10); also the `/review` star choices. |
| `allow_comment`, `comment_required`, `comment_max_length` | `true`, `false`, `1000` | The comment form; `comment_max_length` also limits the `/review` description. |
| `prompt_via` | `"dm_then_channel"` | `"dm"`, `"channel"` (the order channel) or DM with the order channel as fallback. |
| `require_approval` | `false` | `true`: every submitted review waits for a staff decision in `approval_channel` before it is published. |
| `approval_channel` | `"staff_chat"` | Channel key for approval messages; must be a staff or admin channel. Used (and checked) when `require_approval`, `allow_unverified` or `photo_requires_approval` is on. |
| `allow_unverified` | `true` (code default `false`) | `/review` or Write a review without a delivered order to review writes an unverified review that always waits for staff. |
| `unverified_cooldown_days` | `30` | At most one unverified review per member in this many days (0 = only one, ever; 0-3650). |
| `photo_requires_approval` | `true` (code default `false`) | A review sent with a photo always waits for staff approval, also a verified one with `require_approval` off. |

Settings problems (reviews stay hidden until fixed): the two business decisions unset, a missing/unknown channel,
`prompt_via = "dm"` without `features.dm_notifications`, `comment_required` without `allow_comment`, and when a review
can wait for approval (any of the three settings above): no approval channel, an unknown one, or one that is not
staff/admin-only (the approval message shows reviews that are not public). Staff for the approval buttons and
`/reviews`: `permissions.staff_roles`, admins and `checkout.staff_roles`.

## Content keys (`reviews.*`)

All customer-visible text is a content key (full list with limits and placeholders in `docs/CONTENT_CHECKLIST.md`):

- Request: `reviews.prompt.title` (optional), `.description`, `.select_placeholder`, `reviews.button.decline`,
  `reviews.rating.<n>.label|description|emoji|display` (the labels are also the `/review` star choices).
- Comment form: `reviews.comment.title|label` (required with `allow_comment`), `.description|placeholder` (optional).
- Write a review and `/review`: the stars menu `reviews.pick.description|placeholder`,
  `reviews.command.description|stars|image` (Discord receives them when the bot starts; the command is registered only
  when they and the rating labels are set), the form `reviews.write.title|label|photo_label` and the optional
  `.description|placeholder|photo_description`, the replies `reviews.written.description`, `reviews.written_pending.description`, and
  the refusals `reviews.cooldown.description` (`{date}`), `reviews.limit.description` (cooldown 0),
  `reviews.no_order.description` (unverified off), `reviews.image_problem.description` (`{limit}` in MB) with the
  optional `reviews.image_problem.text_field` (heading over the member's description after a sent form),
  `reviews.too_long.description` (`{max}`), `reviews.form_expired.description`.
- Intro panel: `reviews.intro.title`, `.description`, `.button` (Write a review), and the check line that fits the
  settings:
  `reviews.intro.checked_all` (`require_approval` on), `.checked_photos_unverified` (approval off, photos checked and
  unverified reviews on), `.checked_unverified` or `.checked_photos` (only one of them on).
- Published review: `reviews.published.title` (`{stars}`, `{rating}`, `{scale}`), the optional
  `reviews.published.rating_field` (shipped empty: the title shows the stars); verification marker
  `reviews.published.verified_field` + `.verified_value` (optional; set both or neither - one without the other is
  reported and keeps reviews hidden).
- Replies to the request: `reviews.thanks.description`, `reviews.thanks_pending.description` (optional, approval on),
  `reviews.declined.description`, `reviews.already_reviewed.description`, `reviews.expired.description`.
- Approval message (staff business message; required when a review can wait for approval): `reviews.approval.title`
  (`{stars}`, `{rating}`, `{scale}`, `{user_name}`, `{review_number}`), `.rating_field`, `.order_field`, `.no_order`,
  `.customer_field`, `.status_field`, `reviews.approval.status.pending|approved|rejected|removed` (`{staff_mention}`
  for approved/rejected), `reviews.approval.button.approve|reject`, and the optional `reviews.approval.reason_field`.
- Notices (optional; nothing is sent when the description is empty, a title without its description is reported):
  `reviews.notice.approved.title|description` (`{review_link}`),
  `reviews.notice.rejected.title|description|reason_field`.

Staff replies, the reject form and `/reviews` output are operator text (`reviews.ref.*`, `reviews.target.*`,
`reviews.status.*`, `reviews.form.*`, `reviews.decision.*`, `reviews.list.*`, `reviews.cmd.*`, `reviews.setup.*`,
`reviews.intro.*`; overridable with `operator.<key>`). Staff replies name a review by its number and its order's
number ("review #4 (order #12)"; "review #5 (no order)").

## Commands

| Command | Who | What it does |
|---|---|---|
| `/review stars:<n> [photo]` | every member | Opens the private form for the description (and a photo upload without the photo option) and writes the review (see above). Works where the member can type; in #reviews they use the Write a review button. |
| `/reviews list [status]` | staff | The 25 most recently changed reviews with their review number, order (or "no order"), status, rating, member and link. |
| `/reviews remove [order_number] [review]` | staff | Deletes the published review message and closes the review; a pending review's approval message shows "removed"; a request that was not sent yet is cancelled. |
| `/reviews resend <order_number>` | staff | Sends an unanswered review request again. |
| `/reviews approve [order_number] [review]` | staff | Approves a review waiting for approval (same as the Approve button, e.g. when the approval message was deleted). |
| `/reviews reject [order_number] [review] [reason]` | staff | Rejects a review waiting for approval (same as the Reject form). |

`remove`, `approve` and `reject` take the order number or the review number (exactly one); a review without an order
has only its review number (shown in `/reviews list` and on the approval message). The staff tools were `/review
...` before `/review` became the members' command.

## Data

Table `reviews` (rebuilt by `0009_reviews_approval.sql` and again by `0015_reviews_command.sql`, every row kept and
the id sequence kept): `order_id` (NULL = an unverified review; UNIQUE for a real order), `source`
(`prompt | command`), status `prompted | rated | pending_approval | published | rejected | declined`, rating, comment,
the request and published messages, and the moderation record: `approval` (`automatic | approved | rejected`),
`decided_by`, `decided_at`, `decision_reason` (1-500 characters), `approval_channel_id` / `approval_message_id`
(UNIQUE). CHECK constraints make inconsistent states impossible (a pending or rejected review without
rating/comment, a published or submitted review without an approval, a staff decision without who/when, a reason on a
non-rejected review, a message id without its channel, a review without an order that is a request, written another
way than `/review` or Write a review (both `command`), or approved automatically).

Table `review_images` (0015): one photo per review (`review_id`), its type, size and sha256, the bytes (`data`) until
`released_at`. The database backups (`/backup`, the copy before a migration, the `/reset` backup) carry the stored
photos with their reviews; `/reset wipe_shop_data` deletes them with the reviews.

Durable tasks: `reviews.prompt`, `reviews.publish`, `reviews.approval` (post the approval message), `reviews.decided`
(decision follow-ups, scheduled in the decision's transaction). Startup re-queues unpublished submitted reviews and
approval messages that were not posted, and releases stored photos nothing posts any more; `/setup` additionally
posts or updates the intro panel, edits the approval messages of pending reviews to the current text and re-posts
deleted ones.

## Testing in Discord (manual)

1. Run `/setup`: #reviews shows the intro panel with the **Write a review** button and the reviews banner.
2. Without an order, as a member with only the Member role: press **Write a review** -> a private stars menu; pick 5
   -> the form opens with the description and a photo upload; write a description, add a photo and submit -> "our
   team checks it"; #staff-chat shows the approval message with the photo and your name; #reviews shows nothing yet.
   Approve -> #reviews shows "★★★★★ 5/5", the description and the photo, without your name or a verification marker;
   you get the approval notice by DM. Write a review again -> refused until 30 days later (no stars menu). In an open
   ticket, `/review stars:5 photo:<a photo>` does the same.
3. With a delivered order (staff: Mark delivered): `/review stars:4` -> published at once with "Verified purchase /
   Order #N"; the order's review request (if it came) answers "already reviewed". With a photo it waits for Approve
   first.
4. Review request: answer one (rating + comment) -> the review appears in #reviews without your name.
5. `reviews.require_approval = true`, `/reload`: every new review waits in #staff-chat; Reject with a reason -> the
   customer gets the rejection notice.
6. `/reviews list`, `/reviews remove review:<n>`, `/reviews approve order_number:<n>`; as a member without the staff
   role the `/reviews` commands are refused.
7. Restart the bot between any two steps: every button and form keeps working (an open `/review` form too).

## Limitations

- Approval messages do not ping staff roles; they appear in the approval channel like other staff messages.
- An approval message that a staff member deleted is re-posted only by `/setup` (or the review is decided with
  `/reviews approve|reject`).
- The approval notice is sent once the approved review is published; if publication keeps failing (e.g. the bot lost
  access to #reviews) the customer is told only after it succeeds.
- A verified review that waits for approval when its order is refunded can only be rejected (or removed).
- Rejected reviews are final for that order: the customer cannot submit a new review for the same order.
- `/review`'s descriptions and star choices are sent to Discord when the bot starts; a changed text, rating label or
  rating scale needs a restart (`/reload` does not re-register commands).
- Editing an approval message keeps the photo it already shows; a photo is uploaded again only when the message
  lost it (e.g. a re-posted approval message).
- Only the metadata blocks named above are removed from the photo; the bot does not look at what the picture shows
  (a face, a street sign).
- With `photo_requires_approval = false` (and `require_approval` off) a verified review's photo appears in #reviews
  unchecked, like its text.
- `/review` always reviews the member's newest delivered order without a finished review; the review request's text
  says so, but a member with two such orders cannot pick the older one with `/review` (its request still works).
- Where members can type `/review`: every shipped channel members see is read-only for them (`members_readonly`), and
  Discord may not let a member use slash commands in a channel where they cannot send messages, so the intro panel's
  Write a review button is the way in #reviews; `/review` works in an open order or support ticket.
- After the review form was closed without sending, picking the same stars again in the same private menu sends
  nothing (Discord keeps showing the choice, and an ephemeral menu cannot be reset after the form opened): pressing
  Write a review again gives a fresh menu.
- A photo uploaded in the form is checked only when the form is sent (Discord lets members pick any file there and
  does not tell the bot before): a refused one answers with the member's text so it can be sent again.
- The file upload field in forms and the button path have not been tried in a live Discord client yet (docs/MORNING.md
  has the check).
- Not yet verified in a live Discord server (automated tests use the repository's Discord fakes).
