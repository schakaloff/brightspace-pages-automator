"""Read nested course folders without losing their visibility or grouping."""

from unit_overview import BrowserContentAPI


async def read_topic_tree(page, course_id: str, module_id: str, items: list):
    """Return topic metadata and folder descriptions in Brightspace order.

    Embedded Structure entries can be summaries without IsHidden or Url.
    Read each folder separately and leave full topic reads to the collector.
    """
    topics, folders = {}, {}
    seen_modules = {str(module_id)}

    async def walk(children, parent_id, path=(), parent_hidden=False):
        if not isinstance(children, list):
            raise ValueError(f"Folder {parent_id} returned an invalid child list")
        for item in children:
            if not isinstance(item, dict) or not (item.get("Id") or item.get("TopicId")):
                raise ValueError(f"Folder {parent_id} returned an item without an ID")
            item_id = str(item.get("Id") or item.get("TopicId"))
            if item.get("Type") == 0 or "Structure" in item:
                if item_id in seen_modules:
                    raise ValueError(f"Folder {item_id} appears more than once in the unit")
                seen_modules.add(item_id)
                api = BrowserContentAPI(page, course_id, item_id)
                folder = await api.get_module()
                if not isinstance(folder, dict) or str(folder.get("Id")) != item_id:
                    raise ValueError(f"Could not identify folder {item_id}")
                if not isinstance(folder.get("IsHidden"), bool):
                    raise ValueError(f"Folder {item_id} returned no usable visibility")
                if "Description" not in folder or not isinstance(folder["Description"], (dict, type(None))):
                    raise ValueError(f"Folder {item_id} returned no usable description")
                if str(folder.get("ParentModuleId")) != str(parent_id):
                    raise ValueError(f"Folder {item_id} no longer belongs to folder {parent_id}")
                folders[item_id] = folder
                await walk(
                    await api.list_structure(), item_id, path + (item_id,),
                    parent_hidden or folder["IsHidden"],
                )
            else:
                if item_id in topics:
                    raise ValueError(f"Topic {item_id} appears more than once in the unit")
                topics[item_id] = {
                    **item, "ParentModuleId": parent_id,
                    "_FolderPath": path, "_ParentHidden": parent_hidden,
                    "_Order": len(topics),
                }

    await walk(items, str(module_id))
    return topics, folders
