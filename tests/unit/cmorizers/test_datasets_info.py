import os
import datetime
import yamale
import yaml

yaml_folder = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '../../../', 'esmvaltool/cmorizers/data'))

recipes_folder = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '../../../', 'esmvaltool/recipes'))


def test_only_datasets_are_present():
    recipe = yamale.make_data(
        os.path.join(yaml_folder, 'datasets.yml'))
    schema = yamale.make_schema(
        os.path.join(yaml_folder, 'datasets_schema.yml'))
    yamale.validate(schema, recipe)


def test_latest_version_format():
    with open(os.path.join(yaml_folder, 'datasets.yml'), 'r') as file:
        cfg = yaml.safe_load(file)
    for dataset_info in cfg['datasets'].values():
        datetime.datetime.strptime(
            str(dataset_info['last_access']), "%Y%m%d")


def test_datasets_are_added_to_test_recipe():
    with open(os.path.join(yaml_folder, 'datasets.yml'), 'r') as file:
        cfg = yaml.safe_load(file)

    recipe_path = os.path.join(recipes_folder, 'examples/recipe_check_obs.yml')
    with open(recipe_path, 'r') as file:
        recipe = yaml.safe_load(file)

    tested_datasets = set()
    for diagnostic in recipe.get('diagnostics', {}).values():
        for dataset in diagnostic.get('additional_datasets', {}):
            tested_datasets.add(dataset['dataset'])
        for variable in diagnostic.get('variables', {}).values():
            if variable is None:
                continue
            for dataset in variable.get('additional_datasets', {}):
                tested_datasets.add(dataset['dataset'])

    for dataset in cfg['datasets'].keys():
        assert dataset in tested_datasets
