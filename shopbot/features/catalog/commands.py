"""The ``/product`` and ``/category`` slash command groups (catalog managers and admins).

Visibility: ``default_permissions(manage_guild=True)`` hides the group from members without Manage Server
(a convenience only; server admins can change it). Authorization is enforced on every invocation by
``require_staff`` plus ``require_manager`` (admins, or members holding a role in ``catalog.manager_roles``);
general staff without a manager role are refused. Replies are operator text and always ephemeral.

Product images are an https link or an uploaded file (``/product image file:``). An uploaded file is downloaded
and stored in the database (Discord attachment links are signed and expire), then attached to every listing
message that shows it, again on every edit.

``/product remove`` asks for confirmation with buttons (staff-only, routed ``ct:remove``/``ct:remove_no``) and then
archives the product (the same rules as ``/product archive``: refused while pending orders hold units).

``/category create|edit|delete|list`` manage the categories of the #catalog menu and ``/product categories`` puts a
product in one or takes it out (``category_admin``); category options autocomplete by name.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import discord
from discord import app_commands

from shopbot.commands import NotAuthorized, app_of, reply, require_staff
from shopbot.features.catalog import categories, category_admin, repo, service, views
from shopbot.features.catalog.content_keys import CATALOG
from shopbot.ui.discord_render import check_modal, to_modal
from shopbot.ui.spec import ModalSpec

if TYPE_CHECKING:
    from shopbot.context import App

T = TypeVar("T")
MAX_ID = 9_007_199_254_740_991  # Discord integer option maximum (2**53 - 1)
ProductId = app_commands.Range[int, 1, MAX_ID]
CategoryId = app_commands.Range[int, 1, MAX_ID]
CategoryName = app_commands.Range[str, 1, categories.NAME_MAX]
CategoryEmoji = app_commands.Range[str, 1, 40]
CategoryDescription = app_commands.Range[str, 1, categories.DESCRIPTION_MAX]
CategoryPosition = app_commands.Range[int, -categories.POSITION_LIMIT, categories.POSITION_LIMIT]
StockAmount = app_commands.Range[int, -repo.STOCK_MAX, repo.STOCK_MAX]
Position = app_commands.Range[int, -repo.SORT_LIMIT, repo.SORT_LIMIT]
CHOICE_NAME_MAX = 100
AutocompleteCallback = Callable[[discord.Interaction[Any], str], Coroutine[Any, Any, list[app_commands.Choice[int]]]]


def _member(interaction: discord.Interaction[Any]) -> discord.Member | None:
    return interaction.user if isinstance(interaction.user, discord.Member) else None


def require_manager() -> Callable[[T], T]:
    """Command check: admins or members with a ``catalog.manager_roles`` role (in the configured guild)."""

    async def predicate(interaction: discord.Interaction[Any]) -> bool:
        app = app_of(interaction)
        if interaction.guild_id != app.env.guild_id or not service.is_manager(
            app, _member(interaction), interaction.user.id
        ):
            raise NotAuthorized()
        return True

    return app_commands.check(predicate)


async def _ready(interaction: discord.Interaction[Any]) -> bool:
    app = app_of(interaction)
    if app.ready(CATALOG):
        return True
    disabled = service.OpReply(
        app.op("command.feature_disabled", feature=CATALOG), tone="warning", banner=service.REFUSED
    )
    await reply(interaction, service.reply_spec(app, disabled), ephemeral=True)
    return False


async def _run(interaction: discord.Interaction[Any], action: Callable[[], Awaitable[service.OpReply]]) -> None:
    """Defer (the listing refresh may take a moment), run the change, reply with the operator report."""
    if not await _ready(interaction):
        return
    app = app_of(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await action()
    await reply(interaction, service.reply_spec(app, result), ephemeral=True)


async def _send_modal(interaction: discord.Interaction[Any], spec: ModalSpec) -> None:
    check_modal(spec)
    modal = to_modal(spec)
    await interaction.response.send_modal(modal)
    # Submissions are routed by custom_id (restart-safe); drop discord.py's in-memory copy.
    modal.stop()


def _choice_name(app: App, product: repo.Product) -> str:
    label = app.op("catalog.choice", product_id=product.id, name=product.name)
    return label if 1 <= len(label) <= CHOICE_NAME_MAX else product.name


def _autocomplete(include_archived: bool) -> AutocompleteCallback:
    async def complete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[int]]:
        app = app_of(interaction)
        if interaction.guild_id != app.env.guild_id or not service.is_manager(
            app, _member(interaction), interaction.user.id
        ):
            return []
        products = await service.autocomplete_products(app, current, include_archived=include_archived)
        return [app_commands.Choice(name=_choice_name(app, p), value=p.id) for p in products[:25]]

    return complete


def _category_choice(app: App, category: categories.Category) -> str:
    label = app.op("catalog.category.choice", category_id=category.id, name=category.name)
    return label if 1 <= len(label) <= CHOICE_NAME_MAX else category.name


def _category_autocomplete(*, of_product: bool = False) -> AutocompleteCallback:
    """Category names matching what was typed; ``of_product``: only the categories of the product chosen in the same
    command (``/product categories remove:``), when one is chosen."""

    async def complete(interaction: discord.Interaction[Any], current: str) -> list[app_commands.Choice[int]]:
        app = app_of(interaction)
        if interaction.guild_id != app.env.guild_id or not service.is_manager(
            app, _member(interaction), interaction.user.id
        ):
            return []
        product_id = getattr(interaction.namespace, "product_id", None) if of_product else None
        product = product_id if isinstance(product_id, int) else None
        found = await category_admin.autocomplete(app, current, product)
        return [app_commands.Choice(name=_category_choice(app, c), value=c.id) for c in found[:25]]

    return complete


def build_category_group() -> app_commands.Group:
    """``/category``: the categories of the #catalog menu (catalog managers and admins, like ``/product``)."""
    group = app_commands.Group(name="category", description="Categories of the catalog menu (catalog managers).")
    group = app_commands.default_permissions(manage_guild=True)(group)
    group = app_commands.guild_only(group)
    any_category = _category_autocomplete()

    @group.command(name="create", description="Create a category for the #catalog menu.")
    @app_commands.describe(
        name="Its name in the menu (1-50 characters), e.g. Old stock.",
        emoji="An icon name of the bot (e.g. tag) or one emoji.",
        description="Small text under its name in the menu (up to 100 characters).",
        position="Its place in the menu (lower comes first; default: last).",
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def create(
        interaction: discord.Interaction[Any],
        name: CategoryName,
        emoji: CategoryEmoji | None = None,
        description: CategoryDescription | None = None,
        position: CategoryPosition | None = None,
    ) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: category_admin.create(
                app,
                actor_id=interaction.user.id,
                name=name,
                emoji=emoji or "",
                description=description or "",
                position=position,
            ),
        )

    @group.command(name="edit", description="Change a category's name, emoji, description or place in the menu.")
    @app_commands.describe(
        category="The category to change.",
        name="New name (1-50 characters).",
        emoji="An icon name of the bot (e.g. tag) or one emoji; - removes it.",
        description="Small text under its name (up to 100 characters); - removes it.",
        position="Its place in the menu (lower comes first).",
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def edit(
        interaction: discord.Interaction[Any],
        category: CategoryId,
        name: CategoryName | None = None,
        emoji: CategoryEmoji | None = None,
        description: CategoryDescription | None = None,
        position: CategoryPosition | None = None,
    ) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: category_admin.edit(
                app,
                actor_id=interaction.user.id,
                category_id=category,
                name=name,
                emoji=emoji,
                description=description,
                position=position,
            ),
        )

    @group.command(name="delete", description="Delete a category (its items stay in the catalog).")
    @app_commands.describe(category="The category to delete.", confirm="Set to True to delete it.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def delete(interaction: discord.Interaction[Any], category: CategoryId, confirm: bool = False) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: category_admin.delete(app, actor_id=interaction.user.id, category_id=category, confirm=confirm),
        )

    @group.command(name="list", description="List the categories with their items.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def list_(interaction: discord.Interaction[Any]) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        for spec in await category_admin.list_messages(app):
            await reply(interaction, spec, ephemeral=True)

    edit.autocomplete("category")(any_category)
    delete.autocomplete("category")(any_category)
    return group


def build_product_group() -> app_commands.Group:
    group = app_commands.Group(name="product", description="Manage catalog products (catalog managers and admins).")
    group = app_commands.default_permissions(manage_guild=True)(group)
    group = app_commands.guild_only(group)
    active_products = _autocomplete(include_archived=False)
    all_products = _autocomplete(include_archived=True)

    @group.command(name="add", description="Create a product (opens the product form), optionally with its picture.")
    @app_commands.describe(image="The product's picture: PNG, JPEG, GIF or WebP, at most 8 MB (stored by the bot).")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def add(interaction: discord.Interaction[Any], image: discord.Attachment | None = None) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        if image is not None:
            service.hold_add_image(app, str(interaction.id), image)
        form = views.add_form(app.op, service.money_format(app), interaction.id, with_image=image is not None)
        await _send_modal(interaction, form)

    @group.command(name="edit", description="Edit a product's name, price, stock, limit and description.")
    @app_commands.describe(product_id="Product number (see /product list).")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def edit(interaction: discord.Interaction[Any], product_id: ProductId) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        product = await service.load_product(app, product_id)
        if product is None or product.archived:
            error = repo.ProductNotFound(product_id) if product is None else service.ArchivedProduct(product_id)
            await reply(interaction, service.reply_spec(app, service.error_reply(app, error)), ephemeral=True)
            return
        await _send_modal(interaction, views.edit_form(app.op, service.money_format(app), product))

    @group.command(name="price", description="Set a product's price.")
    @app_commands.describe(product_id="Product number.", amount="New price, e.g. 12.50 (in the shop currency).")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def price(interaction: discord.Interaction[Any], product_id: ProductId, amount: str) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: service.set_price(app, actor_id=interaction.user.id, product_id=product_id, amount=amount),
        )

    @group.command(name="stock", description="Set or change a product's stock, or make it unlimited.")
    @app_commands.describe(
        product_id="Product number.",
        mode="set: units on hand; add: add (or remove, if negative) units; unlimited: no stock limit.",
        amount="Number of units (not needed for 'unlimited').",
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def stock(
        interaction: discord.Interaction[Any],
        product_id: ProductId,
        mode: Literal["set", "add", "unlimited"],
        amount: StockAmount | None = None,
    ) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: service.change_stock(
                app, actor_id=interaction.user.id, product_id=product_id, mode=mode, amount=amount
            ),
        )

    @group.command(name="image", description="Set a product's image (https link or uploaded file), or remove it.")
    @app_commands.describe(
        product_id="Product number.",
        url="Permanent https:// image link. Leave out (and give no file) to remove the image.",
        file="PNG, JPEG, GIF or WebP image, at most 8 MB (stored by the bot).",
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def image(
        interaction: discord.Interaction[Any],
        product_id: ProductId,
        url: str = "",
        file: discord.Attachment | None = None,
    ) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: service.set_image(app, actor_id=interaction.user.id, product_id=product_id, url=url, upload=file),
        )

    @group.command(name="toggle", description="Switch a product between active (listed) and inactive (hidden).")
    @app_commands.describe(product_id="Product number.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def toggle(interaction: discord.Interaction[Any], product_id: ProductId) -> None:
        app = app_of(interaction)
        await _run(interaction, lambda: service.toggle_active(app, actor_id=interaction.user.id, product_id=product_id))

    @group.command(name="archive", description="Archive a product for good (hidden, never orderable again).")
    @app_commands.describe(product_id="Product number.", confirm="Set to True to confirm archiving.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def archive(interaction: discord.Interaction[Any], product_id: ProductId, confirm: bool = False) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: service.archive(app, actor_id=interaction.user.id, product_id=product_id, confirm=confirm),
        )

    @group.command(name="remove", description="Remove a product from the catalog (asks for confirmation).")
    @app_commands.describe(product_id="Product number.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def remove(interaction: discord.Interaction[Any], product_id: ProductId) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await service.remove_prompt(app, product_id), ephemeral=True)

    @group.command(name="sort", description="Set a product's position in the listing (lower comes first).")
    @app_commands.describe(
        product_id="Product number.", position="Position number; ties are ordered by product number."
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def sort(interaction: discord.Interaction[Any], product_id: ProductId, position: Position) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: service.set_position(app, actor_id=interaction.user.id, product_id=product_id, position=position),
        )

    @group.command(name="view", description="Show a product's details and its customer card.")
    @app_commands.describe(product_id="Product number.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def view(interaction: discord.Interaction[Any], product_id: ProductId) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        for spec in await service.product_details(app, product_id):
            await reply(interaction, spec, ephemeral=True)

    @group.command(name="list", description="List products (staff view, paginated).")
    @app_commands.describe(include_archived="Also list archived products.")
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def list_(interaction: discord.Interaction[Any], include_archived: bool = False) -> None:
        if not await _ready(interaction):
            return
        app = app_of(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await reply(interaction, await service.admin_list_view(app, 0, include_archived), ephemeral=True)

    @group.command(name="categories", description="Put a product in a category, take it out, or show its categories.")
    @app_commands.describe(
        product_id="Product number.",
        add="A category to put it in.",
        remove="A category to take it out of.",
    )
    @require_staff(service.manager_role_keys)
    @require_manager()
    async def categories_(
        interaction: discord.Interaction[Any],
        product_id: ProductId,
        add: CategoryId | None = None,
        remove: CategoryId | None = None,
    ) -> None:
        app = app_of(interaction)
        await _run(
            interaction,
            lambda: category_admin.product_categories(
                app, actor_id=interaction.user.id, product_id=product_id, add=add, remove=remove
            ),
        )

    edit.autocomplete("product_id")(active_products)
    price.autocomplete("product_id")(active_products)
    stock.autocomplete("product_id")(active_products)
    image.autocomplete("product_id")(active_products)
    toggle.autocomplete("product_id")(active_products)
    archive.autocomplete("product_id")(active_products)
    remove.autocomplete("product_id")(active_products)
    sort.autocomplete("product_id")(active_products)
    view.autocomplete("product_id")(all_products)
    categories_.autocomplete("product_id")(active_products)
    categories_.autocomplete("add")(_category_autocomplete())
    categories_.autocomplete("remove")(_category_autocomplete(of_product=True))
    return group
