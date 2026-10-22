"""GitHub Actions YAML loader shared by the v2 parsers."""
import copy
import re
import yaml


class WorkflowLoader(yaml.SafeLoader):
    pass


# PyYAMLのYAML 1.1ではon/off/yes/noがboolになる．GitHubのonキーを保つ．
WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for key, values in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[key] = [
        (tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"
    ]
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(
                f"duplicate YAML key {key!r}, line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node)
    return result


WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
