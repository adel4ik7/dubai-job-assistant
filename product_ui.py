"""v0.3 private-chat product screens. No network services beyond Telegram replies."""
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler

from db import PROFILE_FIELDS, STATUSES
from services.ai import message_chunks
from services.matcher import analyse_match, format_analysis
from services.product import (APP_LABELS, ENGLISH_LEVELS, PROFILE_LABELS, PrivacyError,
                              UserFiles, profile_gaps, validate_field)

WAIT_FORM, WAIT_SEARCH = 10, 11
END = ConversationHandler.END


def keyboard(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


class ProductUI:
    def __init__(self, db, uploads_dir, main_menu):
        self.db, self.main_menu = db, main_menu
        self.files = UserFiles(db, uploads_dir)

    async def reply(self, update, text, markup=None):
        chunks = message_chunks(text)
        for index, chunk in enumerate(chunks):
            await update.effective_message.reply_text(chunk, reply_markup=markup if index == len(chunks) - 1 else None)

    async def profile(self, update):
        profile = self.db.get_profile(update.effective_user.id)
        text = "Profile\n\n" + ("\n".join(f"{label}: {profile[key] or 'Not specified'}" for key, label in PROFILE_LABELS.items())
                                  if profile else "Create your profile to check application preferences.")
        rows = [[("Edit " + label, "p:edit:" + key)] for key, label in PROFILE_LABELS.items()] if profile else [[("Create profile", "p:create")]]
        rows.append([("Main menu", "p:home")])
        await self.reply(update, text, keyboard(rows))

    async def prompt(self, update, context):
        form = context.user_data["form"]
        field = form["fields"][form["index"]]
        labels = PROFILE_LABELS if form["kind"] == "profile" else APP_LABELS
        rows = []
        choices = STATUSES if field == "status" else ENGLISH_LEVELS if field == "english_level" else ()
        if choices:
            rows = [[(choice, f"p:choice:{form['nonce']}:{index}")] for index, choice in enumerate(choices)]
        if field not in {"company", "role", "full_name", "status"}:
            rows.append([("Skip / clear", f"p:skip:{form['nonce']}")])
        rows.append([("Cancel", "p:home")])
        await self.reply(update, f"{form['index'] + 1}/{len(form['fields'])} — {labels[field]}\nSend a value or use the buttons. /cancel stops without saving.", keyboard(rows))

    async def begin_form(self, update, context, kind, fields, defaults=None):
        context.user_data["form"] = {"kind": kind, "fields": list(fields), "index": 0,
                                     "values": defaults or {}, "nonce": secrets.token_hex(4)}
        await self.prompt(update, context)
        return WAIT_FORM

    async def form_received(self, update, context, value=None):
        form = context.user_data.get("form")
        if not form:
            await self.reply(update, "This form expired. Open /menu.", self.main_menu)
            return END
        field = form["fields"][form["index"]]
        try:
            value = validate_field(field, update.message.text if value is None else value)
        except ValueError as exc:
            await self.reply(update, str(exc))
            return WAIT_FORM
        form["values"][field] = value
        form["index"] += 1
        form["nonce"] = secrets.token_hex(4)
        if form["index"] < len(form["fields"]):
            await self.prompt(update, context)
            return WAIT_FORM
        user_id = update.effective_user.id
        if form["kind"] == "profile":
            self.db.save_profile(user_id, **form["values"])
            context.user_data.pop("form", None)
            await self.profile(update)
        else:
            app_id = self.db.add_application(user_id, **form["values"])
            if form["values"].get("vacancy_text") and context.user_data.get("analysis"):
                context.user_data["analysis"]["saved"] = True
            context.user_data.pop("form", None)
            await self.reply(update, f"Application #{app_id} saved.", self.main_menu)
        return END

    async def cvs(self, update, context, page=0, compare=False):
        rows = self.db.list_resumes(update.effective_user.id, limit=6, offset=page * 5)
        buttons = []
        for row in rows[:5]:
            prefix = "Compare " if compare else "★ " if row["active"] else ""
            callback = f"p:{'compare' if compare else 'cv'}:{row['id']}"
            if compare:
                if not context.user_data.get("analysis"):
                    await self.reply(update, "Analyse a vacancy first.", self.main_menu)
                    return
                callback += ":" + context.user_data["analysis"]["token"]
            buttons.append([(f"{prefix}#{row['id']} {row['filename'][:45]}", callback)])
        if page:
            buttons.append([("Previous", f"p:{'comparepage' if compare else 'cvs'}:{page-1}")])
        if len(rows) > 5:
            buttons.append([("Next", f"p:{'comparepage' if compare else 'cvs'}:{page+1}")])
        if not compare:
            buttons.append([("Upload CV", "upload_cv")])
        buttons.append([("Main menu", "p:home")])
        await self.reply(update, "Choose a CV to compare." if compare else "CVs — ★ marks active. New uploads become active.\n" + ("No CVs yet." if not rows else "Select a CV to activate or delete it."), keyboard(buttons))

    async def applications(self, update, context, page=0):
        selection = context.user_data.get("app_filter", {})
        rows = self.db.list_applications(update.effective_user.id, 6, offset=page * 5, **selection)
        text = "Applications\n" + (f"Filter: {selection}\n" if selection else "")
        buttons = []
        for row in rows[:5]:
            text += f"\n#{row['id']} {row['company']} — {row['role']} · {row['status']}"
            buttons.append([(f"Open #{row['id']}", f"p:app:{row['id']}")])
        if not rows:
            text += "\nNo applications found."
        if page:
            buttons.append([("Previous", f"p:apps:{page-1}")])
        if len(rows) > 5:
            buttons.append([("Next", f"p:apps:{page+1}")])
        buttons += [[("Add", "p:add"), ("Filter", "p:filter"), ("Search", "p:search")],
                    [("Clear filters", "p:clear"), ("Main menu", "p:home")]]
        await self.reply(update, text, keyboard(buttons))

    async def search_received(self, update, context):
        value = (update.message.text or "").strip()
        if not 1 <= len(value) <= 100:
            await self.reply(update, "Enter 1–100 characters, or /cancel.")
            return WAIT_SEARCH
        context.user_data.setdefault("app_filter", {})["search"] = value
        await self.applications(update, context)
        return END

    async def report(self, update, context, vacancy, resume):
        token = secrets.token_hex(4)
        context.user_data["analysis"] = {"vacancy": vacancy, "resume_id": resume["id"], "token": token}
        markup = keyboard([[("Save vacancy/application", f"p:save:{token}")],
                           [("Compare with another CV", f"p:other:{token}")],
                           [("Most important gaps", f"p:gaps:{token}"), ("Profile gaps", f"p:profilegaps:{token}")],
                           [("Main menu", "p:home")]])
        await self.reply(update, f"CV #{resume['id']}: {resume['filename']}\n\n" + format_analysis(analyse_match(resume["extracted_text"], vacancy)), markup)

    async def delete_my_data(self, update, context):
        context.user_data.pop("form", None)
        context.user_data.pop("cv_delete", None)
        context.user_data.pop("ai_action", None)
        token = secrets.token_hex(8)
        context.user_data["delete_confirmation"] = (token, time.monotonic())
        await self.reply(update, "Delete ALL your locally stored profile, CVs/files, applications and usage data? This cannot be undone. Telegram message history is not deleted. Confirmation expires in 5 minutes.",
                         keyboard([[("Yes, delete my data", "p:erase:" + token)], [("Cancel", "p:home")]]))
        return END

    async def buttons(self, update, context):
        data = update.callback_query.data
        if data == "my_applications":
            data = "p:apps:0"
        if not data.startswith("p:"):
            return None
        parts = data.split(":")
        action = parts[1]
        user_id = update.effective_user.id
        if action != "erase":
            context.user_data.pop("delete_confirmation", None)
        if action not in {"deletecv", "erasecv"}:
            context.user_data.pop("cv_delete", None)
        if action not in {"skip", "choice"}:
            context.user_data.pop("form", None)
        try:
            if action == "home":
                await self.reply(update, "Choose a section:", self.main_menu)
            elif action == "profile":
                await self.profile(update)
            elif action == "create":
                return await self.begin_form(update, context, "profile", PROFILE_FIELDS)
            elif action == "edit" and parts[2] in PROFILE_FIELDS:
                return await self.begin_form(update, context, "profile", [parts[2]])
            elif action in {"skip", "choice"}:
                form = context.user_data.get("form")
                if not form or parts[2] != form["nonce"]:
                    await self.reply(update, "This button expired. Use the latest form.")
                    return WAIT_FORM if form else END
                field = form["fields"][form["index"]]
                choices = STATUSES if field == "status" else ENGLISH_LEVELS if field == "english_level" else ()
                value = "-" if action == "skip" else choices[int(parts[3])]
                return await self.form_received(update, context, value)
            elif action in {"cvs", "comparepage"}:
                await self.cvs(update, context, max(0, int(parts[2])), action == "comparepage")
            elif action == "cv":
                resume = self.db.get_resume(user_id, int(parts[2]))
                if not resume:
                    await self.reply(update, "CV not found.", self.main_menu)
                else:
                    await self.reply(update, f"CV #{resume['id']}: {resume['filename']}", keyboard([
                        [("Set active", f"p:active:{resume['id']}")], [("Delete CV", f"p:deletecv:{resume['id']}")], [("Back to CVs", "p:cvs:0")]]))
            elif action == "active":
                ok = self.db.select_resume(user_id, int(parts[2]))
                await self.reply(update, "Active CV selected." if ok else "CV not found.")
                await self.cvs(update, context)
            elif action == "deletecv":
                resume = self.db.get_resume(user_id, int(parts[2]))
                if resume:
                    token = secrets.token_hex(8)
                    context.user_data["cv_delete"] = (token, resume["id"], time.monotonic())
                    await self.reply(update, f"Delete CV #{resume['id']} and its local file?", keyboard([
                        [("Confirm deletion", "p:erasecv:" + token)], [("Cancel", "p:cvs:0")]]))
            elif action == "erasecv":
                pending = context.user_data.pop("cv_delete", None)
                if pending and pending[0] == parts[2] and time.monotonic() - pending[2] < 300:
                    self.files.delete_cv(user_id, pending[1])
                    context.user_data.pop("analysis", None)
                    await self.cvs(update, context)
                else:
                    await self.reply(update, "Confirmation expired. Open CVs again.")
            elif action == "add":
                return await self.begin_form(update, context, "application", APP_LABELS)
            elif action == "apps":
                await self.applications(update, context, max(0, int(parts[2])))
            elif action == "clear":
                context.user_data.pop("app_filter", None)
                await self.applications(update, context)
            elif action == "filter":
                await self.reply(update, "Filter by status:", keyboard([[(s, f"p:filterstatus:{i}")] for i, s in enumerate(STATUSES)]))
            elif action == "filterstatus":
                context.user_data.setdefault("app_filter", {})["status"] = STATUSES[int(parts[2])]
                await self.applications(update, context)
            elif action == "search":
                await self.reply(update, "Enter part of company or role. Search combines with the selected status. /cancel stops.")
                return WAIT_SEARCH
            elif action == "app":
                app = self.db.get_application(user_id, int(parts[2]))
                if app:
                    text = f"Application #{app['id']}\n" + "\n".join(f"{label}: {app[key] or 'Not specified'}" for key, label in APP_LABELS.items())
                    await self.reply(update, text, keyboard([[("Change status", f"p:status:{app['id']}")], [("Back", "p:apps:0")]]))
                else:
                    await self.reply(update, "Application not found.")
            elif action == "status":
                if self.db.get_application(user_id, int(parts[2])):
                    await self.reply(update, "Choose status:", keyboard([[(s, f"p:setstatus:{parts[2]}:{i}")] for i, s in enumerate(STATUSES)]))
            elif action == "setstatus":
                ok = self.db.update_application_status(user_id, int(parts[2]), STATUSES[int(parts[3])])
                await self.reply(update, "Status updated." if ok else "Application not found.", self.main_menu)
            elif action == "dashboard":
                d = self.db.dashboard(user_id)
                await self.reply(update, "Dashboard\n" + "\n".join(f"{label}: {d[key]}" for key, label in (
                    ("total", "Total applications"), ("active", "Active applications"), ("interviews", "Interviews / tests"),
                    ("offers", "Offers"), ("rejections", "Rejections"))) +
                    f"\n\nCurrent interview-stage rate: {d['interview_rate']}%\nOffer rate: {d['offer_rate']}%\n"
                    "Rates use current statuses, not lifetime transitions. Denominator excludes Saved, Withdrawn and unknown legacy statuses; active excludes offers and closed records.", self.main_menu)
            elif action in {"save", "other", "gaps", "profilegaps", "compare"}:
                analysis = context.user_data.get("analysis")
                if not analysis or action != "compare" and parts[2] != analysis["token"]:
                    await self.reply(update, "Analysis expired. Analyse the vacancy again.", self.main_menu)
                    return END
                if action == "compare" and (len(parts) < 4 or parts[3] != analysis["token"]):
                    await self.reply(update, "Comparison expired. Use the latest analysis.", self.main_menu)
                    return END
                if action == "save":
                    if analysis.get("saved"):
                        await self.reply(update, "This vacancy has already been saved.")
                        return END
                    return await self.begin_form(update, context, "application", APP_LABELS,
                                                 {"vacancy_text": analysis["vacancy"]})
                if action == "other":
                    await self.cvs(update, context, compare=True)
                elif action == "compare":
                    resume = self.db.get_resume(user_id, int(parts[2]))
                    if resume:
                        await self.report(update, context, analysis["vacancy"], resume)
                elif action == "profilegaps":
                    await self.reply(update, profile_gaps(self.db.get_profile(user_id), analysis["vacancy"]), self.main_menu)
                else:
                    resume = self.db.get_resume(user_id, analysis["resume_id"])
                    if not resume:
                        await self.reply(update, "CV no longer exists. Analyse again.")
                    else:
                        gaps = analyse_match(resume["extracted_text"], analysis["vacancy"])["important_gaps"]
                        await self.reply(update, "Important gaps (not confirmed in CV)\n" +
                                         ("\n".join("• " + r["label"] + ": " + r["reason"] for r in gaps) or "None identified."), self.main_menu)
            elif action == "erase":
                pending = context.user_data.pop("delete_confirmation", None)
                if not pending or parts[2] != pending[0] or time.monotonic() - pending[1] >= 300:
                    await self.reply(update, "Confirmation expired. Use /delete_my_data again.")
                else:
                    self.files.delete_all(user_id)
                    context.user_data.clear()
                    await self.reply(update, "Your local data and CV files have been deleted. Telegram history is unchanged.", self.main_menu)
            return END
        except PrivacyError as exc:
            await self.reply(update, str(exc), self.main_menu)
            return END
        except (ValueError, IndexError, KeyError):
            await self.reply(update, "This action is no longer valid. Open /menu and try again.", self.main_menu)
            return END
