from __future__ import annotations

import re
import unicodedata
from datetime import datetime, time, timedelta

from dateutil import parser as date_parser

from src.assistant_models import (
    AssistantCommandRequest,
    AssistantCommandResponse,
    BillCreate,
    BillUpdate,
    EventCreate,
    EventUpdate,
    ItemStatus,
    NoteCreate,
    RecurrenceFrequency,
    ReminderCreate,
    ReminderStatus,
    ReminderUpdate,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    TaskCreate,
    TaskUpdate,
)
from src.service import AssistantService


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _match_text(text: str) -> str:
    collapsed = _normalize_text(text).lower()
    normalized = unicodedata.normalize("NFKD", collapsed)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _extract_recurrence(text: str) -> tuple[RecurrenceFrequency | None, int]:
    lowered = _match_text(text)
    for frequency in RecurrenceFrequency:
        if frequency.value in lowered:
            return frequency, 1
    if "every day" in lowered or "daily" in lowered:
        return RecurrenceFrequency.daily, 1
    if "todos los dias" in lowered or "cada dia" in lowered or "todos os dias" in lowered:
        return RecurrenceFrequency.daily, 1
    if "every week" in lowered or "weekly" in lowered:
        return RecurrenceFrequency.weekly, 1
    if "todas las semanas" in lowered or "cada semana" in lowered or "toda semana" in lowered:
        return RecurrenceFrequency.weekly, 1
    if "every month" in lowered or "monthly" in lowered:
        return RecurrenceFrequency.monthly, 1
    if "todos los meses" in lowered or "cada mes" in lowered or "todo mes" in lowered:
        return RecurrenceFrequency.monthly, 1
    if "every year" in lowered or "yearly" in lowered:
        return RecurrenceFrequency.yearly, 1
    if "todos los anos" in lowered or "cada ano" in lowered:
        return RecurrenceFrequency.yearly, 1
    return None, 1


def _parse_datetime_phrase(text: str, now: datetime) -> datetime | None:
    lowered = _match_text(text)
    weekday_aliases = {
        "monday": 0,
        "lunes": 0,
        "segunda": 0,
        "segunda-feira": 0,
        "segunda feira": 0,
        "tuesday": 1,
        "martes": 1,
        "terca": 1,
        "terca-feira": 1,
        "terca feira": 1,
        "wednesday": 2,
        "miercoles": 2,
        "quarta": 2,
        "quarta-feira": 2,
        "quarta feira": 2,
        "thursday": 3,
        "jueves": 3,
        "quinta": 3,
        "quinta-feira": 3,
        "quinta feira": 3,
        "friday": 4,
        "viernes": 4,
        "sexta": 4,
        "sexta-feira": 4,
        "sexta feira": 4,
        "saturday": 5,
        "sabado": 5,
        "sábado": 5,
        "sabado": 5,
        "domingo": 6,
        "sunday": 6,
    }

    def parse_time_fragment(source: str) -> tuple[int, int] | None:
        time_match = re.search(r"(?:\bat\b|\ba\s+las\b|\ba\s+la\b|\bas\b)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", source)
        if not time_match:
            return None
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return hour, minute

    if "tomorrow" in lowered or "manana" in lowered or "amanha" in lowered:
        base = now + timedelta(days=1)
        parsed_time = parse_time_fragment(lowered)
        if parsed_time:
            return datetime.combine(base.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
        return datetime.combine(base.date(), time(hour=9, minute=0))
    if "tonight" in lowered or "esta noche" in lowered or "esta noite" in lowered:
        parsed_time = parse_time_fragment(lowered) or (19, 0)
        return datetime.combine(now.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    if "this evening" in lowered or "esta tarde" in lowered:
        parsed_time = parse_time_fragment(lowered) or (18, 0)
        return datetime.combine(now.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    if "this afternoon" in lowered or "hoy por la tarde" in lowered or "hoje a tarde" in lowered:
        parsed_time = parse_time_fragment(lowered) or (15, 0)
        return datetime.combine(now.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    if "this morning" in lowered or "esta manana" in lowered or "hoy por la manana" in lowered or "hoje de manha" in lowered:
        parsed_time = parse_time_fragment(lowered) or (9, 0)
        return datetime.combine(now.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    if "today" in lowered or "hoy" in lowered or "hoje" in lowered:
        parsed_time = parse_time_fragment(lowered)
        if parsed_time:
            return datetime.combine(now.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    for weekday, index in weekday_aliases.items():
        if weekday in lowered:
            days_ahead = (index - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            parsed_time = parse_time_fragment(lowered) or (9, 0)
            return datetime.combine(target.date(), time(hour=parsed_time[0], minute=parsed_time[1]))
    try:
        return date_parser.parse(text, fuzzy=True, default=now)
    except (ValueError, OverflowError):
        return None


def _split_shopping_items(raw: str) -> list[str]:
    normalized = raw.replace(" and ", ",")
    return [item.strip(" .") for item in normalized.split(",") if item.strip(" .")]


def _extract_bill_amount(text: str) -> float:
    currency_match = re.search(
        r"(?:for|amount)\s+\$?(\d+(?:\.\d{1,2})?)\b(?:\s*(?:dollars|usd))?",
        text,
        flags=re.IGNORECASE,
    )
    if currency_match:
        return float(currency_match.group(1))
    all_numbers = re.findall(r"\b\d+(?:\.\d{1,2})?\b", text)
    if all_numbers:
        return float(all_numbers[-1])
    return 0.0


def _extract_due_segment(text: str) -> str | None:
    match = re.search(
        r"\bdue(?:\s+on)?\s+(.+?)(?=\s+(?:for|amount)\s+\$?\d|\s+(?:daily|weekly|monthly|yearly|every\s+\w+)|$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip(" .")
    return None


def _extract_move_target(text: str, prefix_pattern: str) -> tuple[str, str | None]:
    remainder = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip()
    when_match = re.search(r"\b(?:to|for)\b\s+(.+)$", remainder, flags=re.IGNORECASE)
    if when_match:
        return remainder[: when_match.start()].strip(" ."), when_match.group(1).strip(" .")
    return remainder.strip(" ."), None


def _clean_title_after_date(text: str) -> str:
    cleaned = re.sub(
        r"\b(today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|daily|weekly|monthly|yearly|every day|every week|every month|every year)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bon\s+[A-Za-z0-9,\-/ ]+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _normalize_entity_title(text: str, prefixes: list[str] | None = None, *, drop_leading_to: bool = False) -> str:
    cleaned = _clean_title_after_date(text)
    for phrase in prefixes or []:
        if phrase in cleaned.lower():
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            cleaned = pattern.sub("", cleaned, count=1)
    cleaned = re.sub(r"^(can you|could you|would you|please)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    if drop_leading_to:
        cleaned = re.sub(r"^to\s+", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.strip(" .,:").split())


def _extract_agenda_target(text: str, now: datetime) -> datetime | None:
    lowered = _match_text(text)
    if "tomorrow" in lowered or "manana" in lowered or "amanha" in lowered:
        return now + timedelta(days=1)
    if "today" in lowered or "hoy" in lowered or "hoje" in lowered:
        return now
    weekday_aliases = {
        "monday": 0,
        "lunes": 0,
        "segunda": 0,
        "tuesday": 1,
        "martes": 1,
        "terca": 1,
        "wednesday": 2,
        "miercoles": 2,
        "quarta": 2,
        "thursday": 3,
        "jueves": 3,
        "quinta": 3,
        "friday": 4,
        "viernes": 4,
        "sexta": 4,
        "saturday": 5,
        "sabado": 5,
        "sunday": 6,
        "domingo": 6,
    }
    for weekday, index in weekday_aliases.items():
        if weekday in lowered:
            days_ahead = (index - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return now + timedelta(days=days_ahead)
    parsed = _parse_datetime_phrase(text, now)
    return parsed


def _is_google_calendar_clear_command(text: str) -> bool:
    lowered = _match_text(text)
    has_calendar_target = any(phrase in lowered for phrase in ["google calendar", "calendario", "agenda"])
    has_clear_verb = any(
        phrase in lowered
        for phrase in [
            "clear",
            "empty",
            "remove all",
            "delete all",
            "cancel all",
            "wipe",
            "limpiar",
            "vaciar",
            "borrar todo",
            "cancelar todo",
            "limpar",
        ]
    )
    has_event_hint = any(
        phrase in lowered
        for phrase in [
            "events",
            "appointments",
            "meetings",
            "schedule",
            "calendar",
            "eventos",
            "citas",
            "reuniones",
            "agenda",
            "calendario",
        ]
    )
    return has_calendar_target and has_clear_verb and has_event_hint


def _is_google_calendar_create_command(text: str) -> bool:
    lowered = _match_text(text)
    has_google_calendar = any(phrase in lowered for phrase in ["google calendar", "google calendario"])
    has_create_verb = any(phrase in lowered for phrase in ["add", "put", "schedule", "create", "agrega", "agregar", "anade", "poner", "programa", "crear", "adiciona", "adicionar", "coloca", "agendar", "criar"])
    has_time_hint = any(
        phrase in lowered
        for phrase in ["today", "tomorrow", "tonight", " at ", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    )
    has_time_hint = has_time_hint or any(
        phrase in lowered
        for phrase in [
            "hoy",
            "hoje",
            "manana",
            "amanha",
            "esta noche",
            "esta noite",
            "lunes",
            "martes",
            "miercoles",
            "jueves",
            "viernes",
            "sabado",
            "domingo",
            "segunda",
            "terca",
            "quarta",
            "quinta",
            "sexta",
        ]
    )
    return has_google_calendar and has_create_verb and has_time_hint


def _normalize_google_calendar_event_title(text: str) -> str:
    title = _normalize_entity_title(
        text,
        prefixes=[
        "can you add to my google calendar",
        "could you add to my google calendar",
        "please add to my google calendar",
        "add to my google calendar",
        "add on my google calendar",
        "put on my google calendar",
        "put into my google calendar",
        "schedule on my google calendar",
        "schedule in my google calendar",
        "create in my google calendar",
        "agrega a mi google calendar",
        "agregar a mi google calendar",
        "pon en mi google calendar",
        "programa en mi google calendar",
        "adiciona no meu google calendar",
        "adicionar no meu google calendar",
        "coloca no meu google calendar",
        "agenda no meu google calendar",
        "cria no meu google calendar",
        ],
    )
    title = re.sub(r"^(add|put|schedule|create|agrega|agregar|anade|poner|programa|crear|adiciona|adicionar|coloca|agenda|cria)\s+", "", title, flags=re.IGNORECASE)
    return title or "Event"


def _is_ambiguous_calendar_clear_command(text: str) -> bool:
    lowered = _match_text(text)
    has_calendar_target = any(phrase in lowered for phrase in ["calendar", "schedule", "calendario", "agenda"])
    has_google = "google calendar" in lowered
    has_clear_verb = any(
        phrase in lowered
        for phrase in [
            "clear",
            "empty",
            "remove all",
            "delete all",
            "cancel all",
            "wipe",
            "limpiar",
            "vaciar",
            "borrar todo",
            "cancelar todo",
            "limpar",
        ]
    )
    return has_calendar_target and has_clear_verb and not has_google


def _is_shopping_clear_command(text: str) -> bool:
    lowered = _match_text(text)
    has_shopping_target = any(phrase in lowered for phrase in ["shopping list", "grocery list", "lista de compras", "lista de supermercado"])
    has_clear_verb = any(
        phrase in lowered
        for phrase in [
            "clear",
            "empty",
            "remove all",
            "delete all",
            "wipe",
            "limpiar",
            "vaciar",
            "borrar todo",
            "limpar",
        ]
    )
    return has_shopping_target and has_clear_verb


def _is_shopping_list_query(text: str) -> bool:
    lowered = _match_text(text)
    shopping_phrases = [
        "shopping list",
        "shopping items",
        "my shopping",
        "grocery list",
        "groceries",
        "lista de compras",
        "lista de supermercado",
        "compras",
    ]
    list_phrases = [
        "what is on",
        "what's on",
        "what do i have on",
        "what do i have in",
        "show me",
        "show my",
        "list my",
        "list",
        "mostrar",
        "mostra",
        "muestre",
        "ensina",
        "ensene",
        "que tenho",
        "que hay",
        "que tenho na",
        "que tenho na minha",
        "que tengo en",
        "que tengo en mi",
        "quais itens",
        "what items",
    ]
    return any(shopping in lowered for shopping in shopping_phrases) and any(
        phrase in lowered for phrase in list_phrases
    )


def _extract_shopping_add_items(text: str) -> list[str]:
    cleaned = re.sub(r"^(add|put|buy)\s+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(to|into|on|for|at)\s+(my\s+)?(shopping list|shopping items|shopping|grocery list|groceries)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(a|la|na|no|para a|para a minha|para minha)\s+(lista de compras|lista de supermercado|compras)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(please|por favor)\b", "", cleaned, flags=re.IGNORECASE).strip(" .")
    return [item for item in _split_shopping_items(cleaned) if item]


def _looks_like_shopping_need_statement(text: str) -> bool:
    lowered = _match_text(text)
    prefixes = ["i need ", "necesito ", "preciso "]
    if not any(lowered.startswith(prefix) for prefix in prefixes):
        return False
    if lowered.startswith("i need to "):
        return False
    blocked_need_starts = [
        "necesito pagar ",
        "necesito hacer ",
        "necesito ir ",
        "necesito llamar ",
        "necesito enviar ",
        "preciso pagar ",
        "preciso fazer ",
        "preciso ir ",
        "preciso ligar ",
        "preciso mandar ",
        "preciso enviar ",
    ]
    if any(lowered.startswith(prefix) for prefix in blocked_need_starts):
        return False
    body = re.sub(r"^(i need|necesito|preciso)\s+", "", lowered, flags=re.IGNORECASE).strip(" .")
    if not body:
        return False
    if _parse_datetime_phrase(text, datetime.utcnow()) is not None:
        return False
    if any(token in body for token in [" tomorrow", " today", " manana", " mañana", " amanha", " hoje", " hoy"]):
        return False
    if any(token in body for token in [" to ", " para ", " porque ", " que "]):
        return False
    return "," in body or " and " in body or " y " in body or " e " in body


def _extract_after_keyword(text: str, keyword: str) -> str | None:
    match = re.search(rf"\b{keyword}\b\s+(.+)$", text, flags=re.IGNORECASE)
    return match.group(1).strip(" .") if match else None


def _extract_task_priority(text: str, default_priority: str) -> str:
    lowered = _match_text(text)
    if "high priority" in lowered or "urgent" in lowered or "alta prioridad" in lowered or "alta prioridade" in lowered:
        return "high"
    if "low priority" in lowered or "baja prioridad" in lowered or "baixa prioridade" in lowered:
        return "low"
    if "medium priority" in lowered or "media prioridad" in lowered or "media prioridade" in lowered:
        return "medium"
    return default_priority


def _resolve_match(
    matches: list,
    entity_type: str,
    label_field: str,
) -> AssistantCommandResponse | tuple[object, None]:
    if not matches:
        return AssistantCommandResponse(
            action=f"missing_{entity_type}",
            message=f"I could not find that {entity_type.replace('_', ' ')}.",
        )
    if len(matches) > 1:
        candidates = [getattr(item, label_field) for item in matches[:5]]
        return AssistantCommandResponse(
            action=f"ambiguous_{entity_type}",
            message=(
                f"I found multiple {entity_type.replace('_', ' ')}s that match. "
                f"Please be more specific: {', '.join(candidates)}."
            ),
            data={"candidates": candidates},
        )
    return matches[0], None


def _looks_actionable_but_ambiguous(text: str) -> bool:
    lowered = _match_text(text)
    signals = [
        "remember",
        "save",
        "add",
        "schedule",
        "meeting",
        "appointment",
        "trip",
        "buy",
        "todo",
        "to do",
        "need to",
        "later",
        "recuerda",
        "guardar",
        "agregar",
        "anadir",
        "programar",
        "comprar",
        "necesito",
        "lembra",
        "salvar",
        "adicionar",
        "agendar",
        "preciso",
    ]
    return any(signal in lowered for signal in signals)


def _is_bill_list_query(lowered: str) -> bool:
    bill_terms = [
        "bill",
        "bills",
        "cuenta",
        "cuentas",
        "factura",
        "facturas",
        "conta",
        "contas",
        "fatura",
        "faturas",
    ]
    list_terms = [
        "show",
        "list",
        "what",
        "which",
        "due",
        "to pay",
        "para pagar",
        "que",
        "quais",
        "tenho",
        "tengo",
        "preciso pagar",
        "need to pay",
    ]
    return any(term in lowered for term in bill_terms) and any(term in lowered for term in list_terms)


def handle_command(request: AssistantCommandRequest, service: AssistantService) -> AssistantCommandResponse:
    text = _normalize_text(request.text)
    lowered = _match_text(text)
    now = request.now or service.current_time(service.get_settings())

    if lowered in {"undo", "undo that", "undo last action", "revert that", "never mind", "deshacer", "deshaz eso", "desfazer", "desfaz isso"}:
        result = service.undo_last_action()
        return AssistantCommandResponse(
            action="undo",
            message=result.message,
            created_type=result.entity_type,
            created_id=result.entity_id,
            data=result.data,
        )

    if (
        lowered.startswith("save a note")
        or lowered.startswith("note:")
        or lowered.startswith("save note")
        or lowered.startswith("remember that")
        or lowered.startswith("guarda una nota")
        or lowered.startswith("guardar nota")
        or lowered.startswith("nota:")
        or lowered.startswith("recuerda que")
        or lowered.startswith("salva uma nota")
        or lowered.startswith("salvar nota")
        or lowered.startswith("lembra que")
    ):
        content = re.sub(r"^(save a note|save note|note:|guarda una nota|guardar nota|nota:|recuerda que|salva uma nota|salvar nota|lembra que)\s*:?\s*", "", text, flags=re.IGNORECASE)
        title = content.split(".")[0][:60] or "Quick note"
        note = service.create_note(NoteCreate(title=title, content=content))
        return AssistantCommandResponse(
            action="create_note",
            message=f"Saved note '{note.title}'.",
            created_type="note",
            created_id=note.id,
            data=note.model_dump(mode="json"),
        )

    if re.match(r"^(delete|remove)\s+(my\s+)?note\b", lowered):
        title = re.sub(r"^(delete|remove)\s+(my\s+)?note\s+", "", text, flags=re.IGNORECASE).strip(" .")
        note = service.find_note_by_title(title)
        if note is None:
            return AssistantCommandResponse(action="delete_note", message="I could not find that note.")
        service.delete_note(note.id)
        return AssistantCommandResponse(
            action="delete_note",
            message=f"Deleted note '{note.title}'.",
            created_type="note",
            created_id=note.id,
            data=note.model_dump(mode="json"),
        )

    if any(
        token in lowered
        for token in [
            "what do i have today",
            "what do i have tomorrow",
            "agenda for",
            "my agenda",
            "calendar for today",
            "calendar for tomorrow",
            "check my calendar",
            "check calendar",
            "what's on my calendar",
            "whats on my calendar",
            "what is on my calendar",
            "can you check my calendar",
            "everything for today",
            "everything for tomorrow",
            "what's my day",
            "whats my day",
            "what is my day",
            "what should i do today",
            "what should i do tomorrow",
            "what should i do first",
            "what is the best next action",
            "best next action",
            "plan my day",
            "que tengo hoy",
            "que tengo manana",
            "mi agenda",
            "agenda para hoy",
            "agenda para manana",
            "calendario para hoy",
            "calendario para manana",
            "revisa mi calendario",
            "que hay en mi calendario",
            "planea mi dia",
            "o que tenho hoje",
            "o que tenho amanha",
            "minha agenda",
            "agenda para hoje",
            "agenda para amanha",
            "calendario para hoje",
            "calendario para amanha",
            "verifica meu calendario",
            "checa meu calendario",
            "o que tem no meu calendario",
            "planeja meu dia",
        ]
    ):
        target = _extract_agenda_target(text, now)
        if target is not None:
            agenda = service.get_agenda_for_date(target)
            return AssistantCommandResponse(
                action="agenda",
                message=f"Here is your agenda for {agenda.date}.",
                data=agenda.model_dump(mode="json"),
            )

    if any(token in lowered for token in ["morning briefing", "brief me for today", "daily briefing", "start my day", "resumen de la manana", "resumo da manha", "comeca meu dia", "empieza mi dia"]):
        briefing = service.get_morning_briefing(now=now)
        return AssistantCommandResponse(
            action="morning_briefing",
            message="Here is your morning briefing.",
            data=briefing.model_dump(mode="json"),
        )

    if any(token in lowered for token in ["evening briefing", "wrap up my day", "end of day", "evening wrap up", "resumen de la noche", "resumo da noite", "fim do dia", "fin del dia"]):
        briefing = service.get_evening_briefing(now=now)
        return AssistantCommandResponse(
            action="evening_briefing",
            message="Here is your evening briefing.",
            data=briefing.model_dump(mode="json"),
        )

    if any(token in lowered for token in ["what do i need tomorrow", "prep tomorrow", "prepare tomorrow", "tomorrow briefing", "que necesito manana", "preparame para manana", "o que eu preciso amanha", "me prepara para amanha"]):
        briefing = service.get_tomorrow_briefing(now=now)
        return AssistantCommandResponse(
            action="tomorrow_briefing",
            message="Here is your tomorrow briefing.",
            data=briefing.model_dump(mode="json"),
        )

    if any(token in lowered for token in ["summary", "what's due", "whats due", "what is due", "overview", "resumen", "sumario", "resumo", "visao geral", "vision general"]):
        summary = service.get_summary(now=now)
        return AssistantCommandResponse(
            action="summary",
            message="Here is your current summary.",
            data=summary.model_dump(mode="json"),
        )

    if any(token in lowered for token in ["test my calendar connection", "is my google calendar connected", "check my google calendar connection"]):
        return AssistantCommandResponse(
            action="google_calendar_test_hint",
            message="Use the Google Calendar test endpoint to verify the live connection status.",
            data={"endpoint": "/integrations/google-calendar/test"},
        )

    if _is_google_calendar_create_command(text):
        starts_at = _parse_datetime_phrase(text, now)
        if starts_at is None:
            return AssistantCommandResponse(
                action="create_google_calendar_event_needs_time",
                message="I can add that to Google Calendar, but I need a time like tonight at 7pm or tomorrow at 9am.",
            )
        title = _normalize_google_calendar_event_title(text)
        event = service.create_event(EventCreate(title=title, starts_at=starts_at))
        if service.google_calendar_service is None:
            return AssistantCommandResponse(
                action="google_calendar_unavailable",
                message="I created the event, but Google Calendar is not configured right now.",
                created_type="event",
                created_id=event.id,
                data=event.model_dump(mode="json"),
            )
        result = service.google_calendar_service.sync_local_event(event, settings=service.get_settings())
        service.set_event_google_id(event.id, result.google_event_id)
        synced_event = service.get_event(event.id)
        return AssistantCommandResponse(
            action="create_and_sync_event_google",
            message=f"Added '{synced_event.title}' to your Google Calendar.",
            created_type="event",
            created_id=synced_event.id,
            data={
                "event": synced_event.model_dump(mode="json"),
                "calendar": result.model_dump(mode="json"),
            },
        )

    if _is_ambiguous_calendar_clear_command(text):
        return AssistantCommandResponse(
            action="confirm_calendar_clear_target",
            message="Do you want me to clear your Google Calendar for that day, or just remove local assistant events?",
            data={"text": text},
        )

    if _is_google_calendar_clear_command(text):
        target = _extract_agenda_target(text, now)
        if target is None:
            return AssistantCommandResponse(
                action="clear_google_calendar_needs_date",
                message="I can clear your Google Calendar, but I need a specific day like today, tomorrow, or a named date.",
            )
        result = service.clear_google_calendar_for_date(target)
        date_label = target.strftime("%A, %B %d")
        if result.deleted == 0:
            return AssistantCommandResponse(
                action="clear_google_calendar_day",
                message=f"Your Google Calendar was already clear for {date_label}.",
                data=result.model_dump(mode="json"),
            )
        return AssistantCommandResponse(
            action="clear_google_calendar_day",
            message=f"Cleared {result.deleted} Google Calendar event(s) for {date_label}.",
            data=result.model_dump(mode="json"),
        )

    if _is_shopping_clear_command(text):
        cleared = service.clear_active_shopping_items()
        if not cleared:
            return AssistantCommandResponse(
                action="clear_shopping_list",
                message="Your shopping list is already empty.",
                data={"cleared": 0},
            )
        return AssistantCommandResponse(
            action="clear_shopping_list",
            message=f"Cleared {len(cleared)} shopping item(s) from your shopping list.",
            data={
                "cleared": len(cleared),
                "items": [item.model_dump(mode="json") for item in cleared],
            },
        )

    if any(token in lowered for token in ["do i already have", "is there already"]) and "shopping list" in lowered:
        name = re.sub(r"^(do i already have|is there already)\s+", "", text, flags=re.IGNORECASE)
        name = re.sub(r"\s+(on|in)\s+(my\s+)?shopping list\??$", "", name, flags=re.IGNORECASE).strip(" ?.")
        item = service.find_shopping_item_by_name(name)
        if item is not None and item.status == ItemStatus.active:
            return AssistantCommandResponse(
                action="shopping_item_exists",
                message=f"Yes, {item.name} is already on your shopping list.",
                created_type="shopping_item",
                created_id=item.id,
                data=item.model_dump(mode="json"),
            )
        return AssistantCommandResponse(
            action="shopping_item_exists",
            message=f"No, {name} is not currently on your shopping list.",
            data={"name": name, "exists": False},
        )

    if _is_shopping_list_query(text) or any(token in lowered for token in [
        "what is on my shopping list",
        "what's on my shopping list",
        "what do i have on my shopping list",
        "what do i have in my shopping list",
        "show my shopping list",
        "show me my shopping list",
        "show me my shopping items",
        "show me my shopping",
        "list my shopping list",
    ]):
        items = service.list_shopping_items(status=ItemStatus.active)
        return AssistantCommandResponse(
            action="list_shopping_items",
            message=f"You have {len(items)} active shopping item(s).",
            data={"items": [item.model_dump(mode="json") for item in items]},
        )

    if any(token in lowered for token in [
        "show my tasks",
        "list my tasks",
        "what are my tasks",
        "que tareas tengo",
        "quais tarefas tenho",
        "que tarefas eu tenho",
        "lista minhas tarefas",
        "muestre mis tareas",
    ]):
        tasks = service.list_tasks(status=ItemStatus.active)
        return AssistantCommandResponse(
            action="list_tasks",
            message=f"You have {len(tasks)} active task(s).",
            data={"tasks": [task.model_dump(mode="json") for task in tasks]},
        )

    if _is_bill_list_query(lowered) or any(token in lowered for token in [
        "show my bills",
        "list my bills",
        "what bills do i have",
        "what bills do i have to pay",
        "what do i need to pay",
        "que cuentas tengo para pagar",
        "que facturas tengo para pagar",
        "que contas eu tenho para pagar",
        "quais contas eu tenho para pagar",
        "quais faturas eu tenho para pagar",
        "mostra minhas contas",
        "liste minhas contas",
    ]):
        bills = service.list_bills(status=ItemStatus.active)
        return AssistantCommandResponse(
            action="list_bills",
            message=f"You have {len(bills)} active bill(s).",
            data={"bills": [bill.model_dump(mode="json") for bill in bills]},
        )

    if any(token in lowered for token in [
        "show my reminders",
        "list my reminders",
        "what reminders do i have",
        "que recordatorios tengo",
        "quais lembretes tenho",
        "que lembretes eu tenho",
        "lista meus lembretes",
        "muestre mis recordatorios",
    ]):
        reminders = service.list_reminders(status=ReminderStatus.pending)
        return AssistantCommandResponse(
            action="list_reminders",
            message=f"You have {len(reminders)} pending reminder(s).",
            data={"reminders": [reminder.model_dump(mode="json") for reminder in reminders]},
        )

    if (
        (
            re.match(r"^(add|put|buy)\b", lowered)
            and any(
                token in lowered
                for token in [
                    "shopping list",
                    "shopping items",
                    "shopping",
                    "grocery list",
                    "groceries",
                    "lista de compras",
                    "lista de supermercado",
                    "compras",
                ]
            )
        )
        or _looks_like_shopping_need_statement(text)
    ):
        items = _extract_shopping_add_items(text) if re.match(r"^(add|put|buy)\b", lowered) else _split_shopping_items(
            re.sub(r"^(i need|necesito|preciso)\s+", "", text, flags=re.IGNORECASE).strip(" .")
        )
        created = []
        updated_existing = []
        for item in items:
            quantity_match = re.match(r"(\d+)\s+(.+)", item)
            quantity = quantity_match.group(1) if quantity_match else "1"
            name = quantity_match.group(2) if quantity_match else item
            shopping_item, existed = service.add_or_increment_shopping_item(ShoppingItemCreate(name=name, quantity=quantity))
            if existed:
                updated_existing.append(shopping_item.model_dump(mode="json"))
            else:
                created.append(shopping_item)
        if updated_existing and not created:
            message = f"Updated {len(updated_existing)} existing shopping item(s)."
        elif updated_existing and created:
            message = f"Added {len(created)} new item(s) and updated {len(updated_existing)} existing item(s)."
        else:
            message = f"Added {len(created)} item(s) to your shopping list."
        return AssistantCommandResponse(
            action="create_shopping_items",
            message=message,
            created_type="shopping_item" if created or updated_existing else None,
            created_id=created[0].id if created else None,
            data={
                "items": [item.model_dump(mode="json") for item in created],
                "updated_existing": updated_existing,
            },
        )

    if re.match(r"^(bought|got|completed|check off)\b", lowered):
        name = re.sub(r"^(bought|got|completed|check off)\s+", "", text, flags=re.IGNORECASE)
        name = re.sub(r"\s+(from\s+)?(my\s+)?shopping list$", "", name, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_shopping_item_matches_by_name(name), "shopping_item", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        item, _ = resolved
        updated = service.update_shopping_item(item.id, ShoppingItemUpdate(status=ItemStatus.completed))
        return AssistantCommandResponse(
            action="complete_shopping_item",
            message=f"Marked shopping item '{updated.name}' as completed.",
            created_type="shopping_item",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(remove|delete|take off)\b", lowered) and "shopping list" in lowered:
        name = re.sub(r"^(remove|delete|take off)\s+", "", text, flags=re.IGNORECASE)
        name = re.sub(r"\s+(from\s+)?(my\s+)?shopping list$", "", name, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_shopping_item_matches_by_name(name), "shopping_item", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        item, _ = resolved
        updated = service.update_shopping_item(item.id, ShoppingItemUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="remove_shopping_item",
            message=f"Removed '{updated.name}' from your shopping list.",
            created_type="shopping_item",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if lowered.startswith("remind me") or lowered.startswith("recuerdame") or lowered.startswith("recordame") or lowered.startswith("lembra me") or lowered.startswith("lembre me"):
        recurrence, interval = _extract_recurrence(lowered)
        remind_at = _parse_datetime_phrase(text, now)
        subject = _normalize_entity_title(
            re.sub(r"^(remind me|recuerdame|recordame|lembra me|lembre me)\s*", "", text, flags=re.IGNORECASE),
            drop_leading_to=True,
        ) or "Reminder"
        reminder = service.create_reminder(
            ReminderCreate(
                title=subject.capitalize(),
                remind_at=remind_at or (now + timedelta(hours=1)),
                recurrence=recurrence,
                recurrence_interval=interval,
            )
        )
        return AssistantCommandResponse(
            action="create_reminder",
            message=f"Created a reminder for {reminder.title}.",
            created_type="reminder",
            created_id=reminder.id,
            data=reminder.model_dump(mode="json"),
        )

    if re.match(r"^(cancel|delete|remove)\s+(my\s+)?reminder\b", lowered):
        title = re.sub(r"^(cancel|delete|remove)\s+(my\s+)?reminder\s+", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_reminder_matches_by_title(title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        reminder, _ = resolved
        updated = service.update_reminder(reminder.id, ReminderUpdate(status=ReminderStatus.dismissed))
        return AssistantCommandResponse(
            action="delete_reminder",
            message=f"Dismissed reminder '{updated.title}'.",
            created_type="reminder",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(move|reschedule|change)\s+(my\s+)?reminder\b", lowered):
        title, when_text = _extract_move_target(text, r"^(move|reschedule|change)\s+(my\s+)?reminder\s+")
        resolved = _resolve_match(service.find_reminder_matches_by_title(title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        reminder, _ = resolved
        remind_at = _parse_datetime_phrase(when_text or "", now)
        if remind_at is None:
            return AssistantCommandResponse(action="move_reminder", message="I found the reminder, but I could not parse the new time.")
        updated = service.update_reminder(reminder.id, ReminderUpdate(remind_at=remind_at))
        return AssistantCommandResponse(
            action="move_reminder",
            message=f"Moved reminder '{updated.title}'.",
            created_type="reminder",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(snooze)\b", lowered) and "reminder" in lowered:
        title = re.sub(r"^(snooze)\s+(my\s+)?reminder\s+", "", text, flags=re.IGNORECASE)
        when_text = _extract_after_keyword(title, "to") or _extract_after_keyword(title, "for")
        base_title = re.sub(r"\b(to|for)\b\s+.+$", "", title, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_reminder_matches_by_title(base_title), "reminder", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        reminder, _ = resolved
        remind_at = None
        if when_text:
            delta_match = re.search(r"(\d+)\s+(minute|minutes|hour|hours|day|days)", when_text, flags=re.IGNORECASE)
            if delta_match:
                amount = int(delta_match.group(1))
                unit = delta_match.group(2).lower()
                if "minute" in unit:
                    remind_at = reminder.remind_at + timedelta(minutes=amount)
                elif "hour" in unit:
                    remind_at = reminder.remind_at + timedelta(hours=amount)
                elif "day" in unit:
                    remind_at = reminder.remind_at + timedelta(days=amount)
        if remind_at is None:
            remind_at = _parse_datetime_phrase(when_text or "", now)
        if remind_at is None:
            return AssistantCommandResponse(action="snooze_reminder", message="I found the reminder, but I could not parse the snooze time.")
        updated = service.update_reminder(reminder.id, ReminderUpdate(remind_at=remind_at))
        return AssistantCommandResponse(
            action="snooze_reminder",
            message=f"Snoozed reminder '{updated.title}'.",
            created_type="reminder",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if (
        lowered.startswith("add task")
        or lowered.startswith("create task")
        or lowered.startswith("task:")
        or lowered.startswith("i need to")
        or lowered.startswith("todo:")
        or lowered.startswith("to do:")
        or lowered.startswith("agrega tarea")
        or lowered.startswith("agregar tarea")
        or lowered.startswith("crear tarea")
        or lowered.startswith("tarea:")
        or lowered.startswith("necesito")
        or lowered.startswith("adiciona tarefa")
        or lowered.startswith("adicionar tarefa")
        or lowered.startswith("criar tarefa")
        or lowered.startswith("tarefa:")
        or lowered.startswith("preciso")
        or lowered.startswith("tenho que")
    ):
        settings = service.get_settings()
        recurrence, interval = _extract_recurrence(lowered)
        due_at = _parse_datetime_phrase(text, now)
        priority = _extract_task_priority(text, settings.default_task_priority)
        title = _normalize_entity_title(
            re.sub(r"^(add task|create task|task:|i need to|todo:|to do:|agrega tarea|agregar tarea|crear tarea|tarea:|necesito|adiciona tarefa|adicionar tarefa|criar tarefa|tarefa:|preciso|tenho que)\s*:?\s*", "", text, flags=re.IGNORECASE),
            drop_leading_to=True,
        ) or "Task"
        task = service.create_task(
            TaskCreate(
                title=title,
                due_at=due_at,
                priority=priority,
                recurrence=recurrence,
                recurrence_interval=interval,
            )
        )
        return AssistantCommandResponse(
            action="create_task",
            message=f"Created task '{task.title}'.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    if re.match(r"^(complete|finish|done with)\b", lowered):
        title = re.sub(r"^(complete|finish|done with)\s+(task\s+)?", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_task_matches_by_title(title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        task, _ = resolved
        updated = service.complete_task(task.id)
        return AssistantCommandResponse(
            action="complete_task",
            message=f"Marked task '{updated.title}' as completed.",
            created_type="task",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(cancel|delete|remove)\s+(my\s+)?task\b", lowered):
        title = re.sub(r"^(cancel|delete|remove)\s+(my\s+)?task\s+", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_task_matches_by_title(title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        task, _ = resolved
        updated = service.update_task(task.id, TaskUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_task",
            message=f"Cancelled task '{updated.title}'.",
            created_type="task",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(move|reschedule|change)\s+(my\s+)?task\b", lowered):
        title, when_text = _extract_move_target(text, r"^(move|reschedule|change)\s+(my\s+)?task\s+")
        resolved = _resolve_match(service.find_task_matches_by_title(title), "task", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        task, _ = resolved
        due_at = _parse_datetime_phrase(when_text or "", now)
        if due_at is None:
            return AssistantCommandResponse(action="move_task", message="I found the task, but I could not parse the new time.")
        updated = service.update_task(task.id, TaskUpdate(due_at=due_at))
        return AssistantCommandResponse(
            action="move_task",
            message=f"Moved task '{updated.title}'.",
            created_type="task",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(rename)\b", lowered) and "task" in lowered:
        body = re.sub(r"^(rename)\s+(my\s+)?task\s+", "", text, flags=re.IGNORECASE)
        match = re.search(r"\bto\b\s+(.+)$", body, flags=re.IGNORECASE)
        if match:
            old_title = body[: match.start()].strip(" .")
            new_title = match.group(1).strip(" .")
            resolved = _resolve_match(service.find_task_matches_by_title(old_title), "task", "title")
            if isinstance(resolved, AssistantCommandResponse):
                return resolved
            task, _ = resolved
            updated = service.update_task(task.id, TaskUpdate(title=new_title))
            return AssistantCommandResponse(
                action="rename_task",
                message=f"Renamed task to '{updated.title}'.",
                created_type="task",
                created_id=updated.id,
                data=updated.model_dump(mode="json"),
            )

    if (
        lowered.startswith("create a bill")
        or lowered.startswith("add bill")
        or lowered.startswith("bill:")
        or lowered.startswith("pay bill")
        or lowered.startswith("crear cuenta")
        or lowered.startswith("crear factura")
        or lowered.startswith("agregar cuenta")
        or lowered.startswith("agregar factura")
        or lowered.startswith("conta:")
        or lowered.startswith("fatura:")
        or lowered.startswith("criar conta")
        or lowered.startswith("criar fatura")
        or lowered.startswith("adicionar conta")
        or lowered.startswith("adicionar fatura")
    ):
        recurrence, interval = _extract_recurrence(lowered)
        due_segment = _extract_due_segment(text)
        due_at = _parse_datetime_phrase(due_segment, now) if due_segment else _parse_datetime_phrase(text, now)
        name = re.sub(r"^(create a bill|add bill|bill:|pay bill|crear cuenta|crear factura|agregar cuenta|agregar factura|conta:|fatura:|criar conta|criar fatura|adicionar conta|adicionar fatura)\s*(for|para)?\s*", "", text, flags=re.IGNORECASE)
        name = re.sub(r"\s+due\b.*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+for\s+\$?\d+(?:\.\d{1,2})?.*$", "", name, flags=re.IGNORECASE).strip(" .") or "Bill"
        amount = _extract_bill_amount(text)
        bill = service.create_bill(
            BillCreate(
                name=name,
                amount=amount,
                due_at=due_at or (now + timedelta(days=7)),
                recurrence=recurrence,
                recurrence_interval=interval,
            )
        )
        return AssistantCommandResponse(
            action="create_bill",
            message=f"Created bill '{bill.name}'.",
            created_type="bill",
            created_id=bill.id,
            data=bill.model_dump(mode="json"),
        )

    if re.match(r"^(pay|paid|pagar|pague|paguei|paga|pago)\b", lowered):
        name = re.sub(r"^(pay|paid|pagar|pague|paguei|paga|pago)\s+(bill\s+|cuenta\s+|factura\s+|conta\s+|fatura\s+)?", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_bill_matches_by_name(name), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        bill, _ = resolved
        updated = service.mark_bill_paid(bill.id)
        return AssistantCommandResponse(
            action="pay_bill",
            message=f"Marked bill '{updated.name}' as paid.",
            created_type="bill",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(cancel|delete|remove)\s+(my\s+)?bill\b", lowered):
        name = re.sub(r"^(cancel|delete|remove)\s+(my\s+)?bill\s+", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_bill_matches_by_name(name), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        bill, _ = resolved
        updated = service.update_bill(bill.id, BillUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_bill",
            message=f"Cancelled bill '{updated.name}'.",
            created_type="bill",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(change|update)\b", lowered) and "bill" in lowered:
        body = re.sub(r"^(change|update)\s+", "", text, flags=re.IGNORECASE).strip()
        bill_name = re.sub(r"\s+bill\b.*$", "", body, flags=re.IGNORECASE).strip(" .")
        if not bill_name:
            bill_name_match = re.search(r"(?:change|update)\s+(.+?)\s+bill", text, flags=re.IGNORECASE)
            bill_name = bill_name_match.group(1).strip(" .") if bill_name_match else ""
        resolved = _resolve_match(service.find_bill_matches_by_name(bill_name), "bill", "name")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        bill, _ = resolved
        updates = {}
        amount_match = re.search(r"\bto\s+\$?(\d+(?:\.\d{1,2})?)", text, flags=re.IGNORECASE)
        due_match = re.search(r"\bdue\s+(?:to|on)?\s+(.+)$", text, flags=re.IGNORECASE)
        if amount_match:
            updates["amount"] = float(amount_match.group(1))
        if due_match:
            due_at = _parse_datetime_phrase(due_match.group(1), now)
            if due_at is not None:
                updates["due_at"] = due_at
        if updates:
            updated = service.update_bill(bill.id, BillUpdate(**updates))
            return AssistantCommandResponse(
                action="update_bill",
                message=f"Updated bill '{updated.name}'.",
                created_type="bill",
                created_id=updated.id,
                data=updated.model_dump(mode="json"),
            )

    if (
        lowered.startswith("schedule")
        or lowered.startswith("create event")
        or lowered.startswith("meeting with")
        or lowered.startswith("book")
        or lowered.startswith("programa")
        or lowered.startswith("crear evento")
        or lowered.startswith("reunion con")
        or lowered.startswith("agenda")
        or lowered.startswith("criar evento")
        or lowered.startswith("reuniao com")
        or lowered.startswith("marcar")
    ):
        starts_at = _parse_datetime_phrase(text, now) or (now + timedelta(days=1))
        if lowered.startswith("meeting with") or lowered.startswith("reunion con") or lowered.startswith("reuniao com"):
            title = _normalize_entity_title(text) or "Meeting"
        else:
            title = _normalize_entity_title(
                re.sub(r"^(schedule|create event|book|programa|crear evento|agenda|criar evento|marcar)\s*", "", text, flags=re.IGNORECASE),
            ) or "Event"
        event = service.create_event(EventCreate(title=title.capitalize(), starts_at=starts_at))
        return AssistantCommandResponse(
            action="create_event",
            message=f"Scheduled '{event.title}'.",
            created_type="event",
            created_id=event.id,
            data=event.model_dump(mode="json"),
        )

    if re.match(r"^(cancel|delete|remove)\s+(my\s+)?event\b", lowered):
        title = re.sub(r"^(cancel|delete|remove)\s+(my\s+)?event\s+", "", text, flags=re.IGNORECASE).strip(" .")
        resolved = _resolve_match(service.find_event_matches_by_title(title), "event", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        event, _ = resolved
        updated = service.update_event(event.id, EventUpdate(status=ItemStatus.cancelled))
        return AssistantCommandResponse(
            action="delete_event",
            message=f"Cancelled event '{updated.title}'.",
            created_type="event",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if re.match(r"^(move|reschedule|change)\s+(my\s+)?event\b", lowered):
        title, when_text = _extract_move_target(text, r"^(move|reschedule|change)\s+(my\s+)?event\s+")
        resolved = _resolve_match(service.find_event_matches_by_title(title), "event", "title")
        if isinstance(resolved, AssistantCommandResponse):
            return resolved
        event, _ = resolved
        starts_at = _parse_datetime_phrase(when_text or "", now)
        if starts_at is None:
            return AssistantCommandResponse(action="move_event", message="I found the event, but I could not parse the new time.")
        updated = service.update_event(event.id, EventUpdate(starts_at=starts_at))
        return AssistantCommandResponse(
            action="move_event",
            message=f"Moved event '{updated.title}'.",
            created_type="event",
            created_id=updated.id,
            data=updated.model_dump(mode="json"),
        )

    if lowered.startswith("complete task "):
        task_id = text.split()[-1]
        task = service.complete_task(task_id)
        return AssistantCommandResponse(
            action="complete_task",
            message=f"Marked task '{task.title}' as completed.",
            created_type="task",
            created_id=task.id,
            data=task.model_dump(mode="json"),
        )

    return AssistantCommandResponse(
        action="unknown",
        message=(
            "I could not confidently classify that yet. "
            "Should I save this as a task, reminder, event, shopping item, or note?"
            if _looks_actionable_but_ambiguous(text)
            else "I could not confidently parse that command yet."
        ),
        data={"text": text},
    )
