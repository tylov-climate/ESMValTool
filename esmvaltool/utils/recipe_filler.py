"""
Fill in a blank recipe with additional datasets.

Tool to obtain a set of additional datasets when given a blank recipe.
The blank recipe should contain, to the very least, a list of diagnostics
each with their variable(s). Example of minimum settings:

diagnostics:
  diagnostic:
    variables:
      ta:
        mip: Amon
        start_year: 1850
        end_year: 1900

Note that the tool will exit if any of these minimum settings are missing!

Key features:

- you can add as many variable parameters as are needed; if not added, the
  tool will use the "*" wildcard and find all available combinations;
- you can restrict the number of datasets to be looked for with the `dataset:`
  key for each variable, pass a list of datasets as value, e.g.
  `dataset: [MPI-ESM1-2-LR, MPI-ESM-LR]`;
- you can specify a pair of experiments eg `exp: [rcp26, rcp85]`
  for each variable; this will look for each available dataset per experiment
  and assemble an aggregated data stretch from each experiment; equivalent to
  esmvaltool's syntax of multiple experiments; this option needs an ensemble
  to be declared explicitly; it will return no entry if there are gaps in data
- `start_year` and `end_year` are mandatory and are used to filter out the
  datasets that don't have data in the interval;
- `config-user: rootpath: CMIPX` may be a list, rootpath lists are supported;

Caveats:

- the tool doesn't yet work for derived variables;
- operation restricted to CMIP data.

Have fun!
"""
import argparse
import itertools
import logging
import os
import shutil
import sys
from glob import glob

import yaml
from ruamel.yaml import YAML
from esmvalcore._config import (configure_logging, read_config_developer_file,
                                read_config_user_file)
from esmvalcore.cmor.table import CMOR_TABLES

logger = logging.getLogger(__name__)

HEADER = r"""
______________________________________________________________________
          _____ ____  __  ____     __    _ _____           _
         | ____/ ___||  \/  \ \   / /_ _| |_   _|__   ___ | |
         |  _| \___ \| |\/| |\ \ / / _` | | | |/ _ \ / _ \| |
         | |___ ___) | |  | | \ V / (_| | | | | (_) | (_) | |
         |_____|____/|_|  |_|  \_/ \__,_|_| |_|\___/ \___/|_|
______________________________________________________________________

""" + __doc__

dataset_order = [
    'dataset', 'project', 'exp', 'mip', 'ensemble', 'grid', 'start_year',
    'end_year'
]

# cmip eras
cmip_eras = ["CMIP5", "CMIP6"]

# The base dictionairy (all wildcards):
base_dict = {
    'institute': '*',
    'dataset': '*',
    'project': '*',
    'exp': '*',
    'frequency': '*',
    'ensemble': '*',
    'mip': '*',
    'modeling_realm': '*',
    'short_name': '*',
    'grid': '*',
    'start_year': '*',
    'end_year': '*',
    'activity': '*',
}


def _get_site_rootpath(cmip_era):
    """Get site (drs) from config-user.yml."""
    config_yml = get_args().config_file
    with open(config_yml, 'r') as yamf:
        yamlconf = yaml.safe_load(yamf)
    drs = yamlconf['drs'][cmip_era]
    rootdir = yamlconf['rootpath'][cmip_era]
    logger.debug(f"{cmip_era} root directory {rootdir}")
    if drs == 'default' and 'default' in yamlconf['rootpath']:
        rootdir = yamlconf['rootpath']['default']
        logger.debug("Using drs default and "
                     f"default: {rootdir} data directory")

    return drs, rootdir


def _get_input_dir(cmip_era):
    """Get input_dir from config-developer.yml."""
    site = _get_site_rootpath(cmip_era)[0]
    yamlconf = read_config_developer_file()

    return yamlconf[cmip_era]['input_dir'][site]


def _get_input_file(cmip_era):
    """Get input_file from config-developer.yml."""
    yamlconf = read_config_developer_file()
    return yamlconf[cmip_era]['input_file']


def _determine_basepath(cmip_era):
    """Determine a basepath."""
    if isinstance(_get_site_rootpath(cmip_era)[1], list):
        rootpaths = _get_site_rootpath(cmip_era)[1]
    else:
        rootpaths = [_get_site_rootpath(cmip_era)[1]]
    basepaths = []
    for rootpath in rootpaths:
        if _get_input_dir(cmip_era) != os.path.sep:
            basepath = os.path.join(rootpath, _get_input_dir(cmip_era),
                                    _get_input_file(cmip_era))
        else:
            basepath = os.path.join(rootpath, _get_input_file(cmip_era))
        while basepath.find('//') > -1:
            basepath = basepath.replace('//', '/')
        basepaths.append(basepath)
    logger.debug(f"We will look for files of patterns {basepaths}")

    return basepaths


def _overlapping_datasets(files, all_years, start_year, end_year):
    """Process overlapping datasets and check for avail data in time range."""
    valid_files = []
    ay_sorted = sorted(all_years)
    if ay_sorted[0] <= start_year and ay_sorted[-1] >= end_year:
        yr_pairs = sorted(
            [all_years[i:i + 2] for i in range(0, len(all_years), 2)])
        yr_pairs = list(k for k, _ in itertools.groupby(yr_pairs))
        d_y = [
            yr_pairs[j][1] - yr_pairs[j + 1][0]
            for j in range(len(yr_pairs) - 1)
        ]
        gaps = [c for c in d_y if c < -1]
        if not gaps:
            valid_files = files
            logger.info("Contiguous data from multiple experiments.")
        else:
            logger.warning("Data from multiple exps has >1 year gaps! ")
            logger.debug(f"Start {start_year}/end {end_year} requested - "
                         f"files covering {yr_pairs} found.")

    return valid_files


def filter_years(files, start_year, end_year, overlap=False):
    """
    Filter out files that are outside requested time range.

    Nifty function that takes a list of files and two years
    as arguments; it will build a series of filter dictionaries
    and check if data is available for the entire interval;
    it will return a single file per dataset, the first file
    in the list of files that cover the specified interval;
    optional argument `overlap` used if multiple experiments are
    used and overlap between datasets is present.

    Parameters
    ----------
    files: list
        A list of files that need filtering by requested time range.

    start_year: int
        Integer start year of requested range.

    end_year: int
        Integer end year of requested range.

    overlap: bool
        Flag if datasets overlap; defaults to False.

    Returns
    -------
    list
        List of files which have been identified as falling in
        the requested time range; if multiple files within time range
        per dataset, the first file will be returned.

    """
    valid_files = []
    available_years = {}

    if not files:
        return valid_files

    all_files_roots = [("").join(fil.split("_")[0:-1]) for fil in files]
    for fil in files:
        available_years[("").join(fil.split("_")[0:-1])] = []
    for fil in files:
        available_years[("").join(fil.split("_")[0:-1])].append(
            fil.split("_")[-1].strip(".nc").split("-"))

    all_years = []
    for root, yr_list in available_years.items():
        actual_years = []
        yr_list = list(itertools.chain.from_iterable(yr_list))
        for year in yr_list:
            if len(year) == 4:
                actual_years.append(int(year))
            else:
                actual_years.append(int(year[0:4]))
        actual_years = sorted(actual_years)
        all_years.extend(actual_years)
        if not overlap:
            actual_years = sorted(list(set(actual_years)))
            if actual_years[0] <= start_year and actual_years[-1] >= end_year:
                idx = all_files_roots.index(root)
                valid_files.append(files[idx])

    # multiple experiments to complete each other
    if overlap:
        valid_files = _overlapping_datasets(files, all_years, start_year,
                                            end_year)

    if not valid_files:
        logger.warning("No data found to fully cover start "
                       f"{start_year} / end {end_year} as requested!")

    return valid_files


def _resolve_latestversion(dirname_template):
    """Resolve the 'latestversion' tag."""
    if '{latestversion}' not in dirname_template:
        return dirname_template

    # Find latest version
    part1, part2 = dirname_template.split('{latestversion}')
    part2 = part2.lstrip(os.sep)
    part1_contents = glob(part1)
    if part1_contents:
        versions = os.listdir(part1_contents[0])
        versions.sort(reverse=True)
        for version in ['latest'] + versions:
            dirname = os.path.join(part1, version, part2)
            if glob(dirname):
                return dirname

    return dirname_template


def list_all_files(file_dict, cmip_era):
    """
    List all files that match the dataset dictionary.

    Function that returnes all files that are determined by a
    file_dict dictionary; file_dict is keyed on usual parameters
    like `dataset`, `project`, `mip` etc; glob.glob is used
    to find files; speedup is achieved by replacing wildcards
    with values from CMOR tables.

    Parameters
    ----------
    file_dict: dict
        Dictionary to hold dataset specifications.

    cmip_era: str
        Either CMIP5 or CMIP6.

    Returns
    -------
    list:
        List of found files.

    """
    mip = file_dict['mip']
    short_name = file_dict['short_name']
    try:
        frequency = CMOR_TABLES[cmip_era].get_variable(mip,
                                                       short_name).frequency
        realms = CMOR_TABLES[cmip_era].get_variable(mip,
                                                    short_name).modeling_realm
    except AttributeError:
        logger.warning(f"Could not find {cmip_era} CMOR table "
                       f"for variable {short_name} with mip {mip}")
        return []
    file_dict['frequency'] = frequency

    basepaths = _determine_basepath(cmip_era)
    all_files = []

    for basepath in basepaths:
        new_path = basepath[:]

        # could have multiple realms
        for realm in realms:
            file_dict['modeling_realm'] = realm

            # load all the files in the custom dict
            for key, value in file_dict.items():
                new_path = new_path.replace('{' + key + '}', str(value))
            new_path = _resolve_latestversion(new_path)
            if new_path.startswith("~"):
                new_path = os.path.expanduser(new_path)
                if not new_path.startswith(os.sep):
                    logger.error("Could not expand ~ to user home dir "
                                 "please expand it in the config user file!")
                    sys.exit(1)
                logger.warning(f"Expanding path to {new_path}")

            # Globs all the wildcards into a list of files.
            files = glob(new_path)
            all_files.extend(files)
    if not all_files:
        logger.warning("Could not find any file for data specifications.")

    return all_files


def _file_to_recipe_dataset(fn, cmip_era, file_dict):
    """Convert a filename to an recipe ready dataset."""
    # Add the obvious ones - ie the one you requested!
    output_dataset = {}
    output_dataset['project'] = cmip_era
    for key, value in file_dict.items():
        if value == '*':
            continue
        if key in dataset_order:
            output_dataset[key] = value

    # Split file name and base path into directory structure and filenames.
    basefiles = _determine_basepath(cmip_era)
    _, fnfile = os.path.split(fn)

    for basefile in basefiles:
        _, basefile = os.path.split(basefile)
        # Some of the key words include the splitting character '_' !
        basefile = basefile.replace('short_name', 'shortname')
        basefile = basefile.replace('start_year', 'startyear')
        basefile = basefile.replace('end_year', 'endyear')

        # Assume filename is separated by '_'
        basefile_split = [key.replace("{", "") for key in basefile.split('_')]
        basefile_split = [key.replace("}", "") for key in basefile_split]
        fnfile_split = fnfile.split('_')

        # iterate through directory structure looking for useful bits.
        for base_key, fn_key in zip(basefile_split, fnfile_split):
            if base_key == '*.nc':
                fn_key = fn_key.replace('.nc', '')
                start_year, end_year = fn_key.split('-')
                output_dataset['start_year'] = start_year
                output_dataset['end_year'] = end_year
            elif base_key == "ensemble*.nc":
                output_dataset['ensemble'] = fn_key
            elif base_key == "grid*.nc":
                output_dataset['grid'] = fn_key
            elif base_key not in ["shortname", "ensemble*.nc", "*.nc"]:
                output_dataset[base_key] = fn_key
    if "exp" in file_dict:
        if isinstance(file_dict["exp"], list):
            output_dataset["exp"] = file_dict["exp"]

    return output_dataset


def _remove_duplicates(add_datasets):
    """
    Remove accidental duplicates.

    Close to 0% chances this will ever be used.
    May be used when there are actual duplicates in data
    storage, we've seen these before, but seldom.
    """
    datasets = []
    seen = set()

    for dataset in add_datasets:
        orig_exp = dataset["exp"]
        dataset["exp"] = str(dataset["exp"])
        tup_dat = tuple(dataset.items())
        if tup_dat not in seen:
            seen.add(tup_dat)
            dataset["exp"] = orig_exp
            datasets.append(dataset)

    return datasets


def _check_recipe(recipe_dict):
    """Perform a quick recipe check for mandatory fields."""
    do_exit = False
    if "diagnostics" not in recipe_dict:
        logger.error("Recipe missing diagnostics section.")
        do_exit = True
    for diag_name, diag in recipe_dict["diagnostics"].items():
        if "variables" not in diag:
            logger.error(f"Diagnostic {diag_name} missing variables.")
            do_exit = True
        for var_name, var_pars in diag["variables"].items():
            if "mip" not in var_pars:
                logger.error(f"Variable {var_name} missing mip.")
                do_exit = True
            if "start_year" not in var_pars:
                logger.error(f"Variable {var_name} missing start_year.")
                do_exit = True
            if "end_year" not in var_pars:
                logger.error(f"Variable {var_name} missing end_year.")
                do_exit = True
            if "exp" in var_pars:
                if isinstance(var_pars["exp"],
                              list) and "ensemble" not in var_pars:
                    logger.error("Asking for experiments list for ")
                    logger.error(f"variable {var_name} - you need to ")
                    logger.error("define an ensemble for this case.")
                    do_exit = True
    if do_exit:
        logger.error("Please fix the issues in recipe and rerun. Exiting.")
        sys.exit(1)


def _check_config_file(user_config_file):
    """Perform a quick recipe check for mandatory fields."""
    do_exit = False
    if "rootpath" not in user_config_file:
        logger.error("Config file missing rootpath section.")
        do_exit = True
    if "drs" not in user_config_file:
        logger.error("Config file missing drs section.")
        do_exit = True
    for proj in cmip_eras:
        if proj not in user_config_file["rootpath"].keys():
            logger.error(f"Config file missing rootpath for {proj}")
            do_exit = True
        if proj not in user_config_file["drs"].keys():
            logger.error(f"Config file missing drs for {proj}")
            do_exit = True
    if do_exit:
        logger.error("Please fix issues in config file and rerun. Exiting.")
        sys.exit(1)


def _parse_recipe_to_dicts(yamlrecipe):
    """Parse a recipe's variables into a dictionary of dictionairies."""
    output_dicts = {}
    for diag in yamlrecipe['diagnostics']:
        for variable, var_dict in yamlrecipe['diagnostics'][diag][
                'variables'].items():
            new_dict = base_dict.copy()
            for var_key, var_value in var_dict.items():
                if var_key in new_dict:
                    new_dict[var_key] = var_value
            output_dicts[(diag, variable)] = new_dict

    return output_dicts


def _add_datasets_into_recipe(additional_datasets, output_recipe):
    """Add the datasets into a new recipe."""
    yaml = YAML()
    yaml.default_flow_style = False
    with open(output_recipe, 'r') as yamlfile:
        cur_yaml = yaml.load(yamlfile)
        for diag_var, add_dat in additional_datasets.items():
            if add_dat:
                if 'additional_datasets' in cur_yaml['diagnostics']:
                    cur_yaml['diagnostics'][diag_var[0]]['variables'][
                        diag_var[1]]['additional_datasets'].extend(add_dat)
                else:
                    cur_yaml['diagnostics'][diag_var[0]]['variables'][
                        diag_var[1]]['additional_datasets'] = add_dat
    if cur_yaml:
        with open(output_recipe, 'w') as yamlfile:
            yaml.dump(cur_yaml, yamlfile)


def _find_all_datasets(recipe_dict, cmip_eras):
    """Find all datasets explicitly."""
    datasets = []
    for cmip_era in cmip_eras:
        if cmip_era == "CMIP6":
            activity = "CMIP"
        else:
            activity = ""
        drs, site_path = _get_site_rootpath(cmip_era)
        if drs in ["default", "ETHZ", "SMHI", "RCAST", "BSC"]:
            logger.info(f"DRS is {drs}; filter on dataset disabled.")
            datasets = ["*"]
        else:
            if drs in ["BADC", "DKRZ", "CP4CDS"]:
                institutes_path = os.path.join(site_path, activity)
            elif drs in ["ETHZ", "RCAST"]:
                exp = recipe_dict["exp"][0]
                if exp == "*":
                    exp = "piControl"  # all institutes have piControl
                mip = recipe_dict["mip"]
                var = recipe_dict["short_name"]
                institutes_path = os.path.join(site_path, exp, mip, var)
            if not os.path.isdir(institutes_path):
                logger.warning(f"Path to data {institutes_path} "
                               "does not exist; will look everywhere.")
                datasets = ["*"]
            institutes = os.listdir(institutes_path)
            for institute in institutes:
                datasets.extend(
                    os.listdir(os.path.join(site_path, activity, institute)))
    return datasets


def _get_exp(recipe_dict):
    """Get the correct exp as list of single or multiple exps."""
    if isinstance(recipe_dict["exp"], list):
        exps_list = recipe_dict["exp"]
        logger.info(f"Multiple {exps_list} experiments requested")
    else:
        exps_list = [recipe_dict["exp"]]
        logger.info(f"Single {exps_list} experiment requested")

    return exps_list


def _get_datasets(recipe_dict, cmip_eras):
    """Get the correct datasets as list if needed."""
    if recipe_dict["dataset"] == "*":
        datasets = _find_all_datasets(recipe_dict, cmip_eras)
        return datasets
    if isinstance(recipe_dict['dataset'], list):
        datasets = recipe_dict['dataset']
        logger.info(f"Multiple {datasets} datasets requested")
    else:
        datasets = [recipe_dict['dataset']]
        logger.info(f"Single {datasets} dataset requested")

    return datasets


def get_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recipe', help='Path/name of yaml pilot recipe file')
    parser.add_argument('-c',
                        '--config-file',
                        default=os.path.join(os.environ["HOME"], '.esmvaltool',
                                             'config-user.yml'),
                        help='User configuration file')

    parser.add_argument('-o',
                        '--output',
                        default=os.path.join(os.getcwd(),
                                             'recipe_autofilled.yml'),
                        help='Output recipe, default recipe_autofilled.yml')

    args = parser.parse_args()
    return args


def _get_timefiltered_files(recipe_dict, exps_list, cmip_era):
    """Obtain all files that correspond to requested time range."""
    # multiple experiments allowed, complement data from each exp
    if len(exps_list) > 1:
        files = []
        for exp in exps_list:
            recipe_dict["exp"] = exp
            files.extend(list_all_files(recipe_dict, cmip_era))
        files = filter_years(files,
                             recipe_dict["start_year"],
                             recipe_dict["end_year"],
                             overlap=True)
        recipe_dict["exp"] = exps_list

    else:
        files = list_all_files(recipe_dict, cmip_era)
        files = filter_years(files, recipe_dict["start_year"],
                             recipe_dict["end_year"])

    return files


def run():
    """Run the `recipe_filler` tool. Help in __doc__ and via --help."""
    # Get arguments
    args = get_args()
    input_recipe = args.recipe
    output_recipe = args.output
    cmip_eras = ["CMIP5", "CMIP6"]

    # read the config file
    config_user = read_config_user_file(args.config_file,
                                        'recipe_filler',
                                        options={})

    # configure logger
    run_dir = os.path.join(config_user['output_dir'], 'recipe_filler')
    if not os.path.isdir(run_dir):
        os.makedirs(run_dir)
    log_files = configure_logging(output_dir=run_dir,
                                  console_log_level=config_user['log_level'])
    logger.info(HEADER)
    logger.info(f"Using user configuration file: {args.config_file}")
    logger.info(f"Using pilot recipe file: {input_recipe}")
    logger.info(f"Writing filled out recipe to: {output_recipe}")
    log_files = "\n".join(log_files)
    logger.info(f"Writing program log files to:\n{log_files}")

    # check config user file
    _check_config_file(config_user)

    # parse recipe
    with open(input_recipe, 'r') as yamlfile:
        yamlrecipe = yaml.safe_load(yamlfile)
        _check_recipe(yamlrecipe)
        recipe_dicts = _parse_recipe_to_dicts(yamlrecipe)

    # Create a list of additional_datasets for each diagnostic/variable.
    additional_datasets = {}
    for (diag, variable), recipe_dict in recipe_dicts.items():
        logger.info("Looking for data for "
                    f"variable {variable} in diagnostic {diag}")
        new_datasets = []
        if "short_name" not in recipe_dict:
            recipe_dict['short_name'] = variable

        # adjust cmip era if needed
        if recipe_dict['project'] != "*":
            cmip_eras = [recipe_dict['project']]

        # get datasets depending on user request; always a list
        datasets = _get_datasets(recipe_dict, cmip_eras)

        # get experiments depending on user request; always a list
        exps_list = _get_exp(recipe_dict)

        # loop through datasets
        for dataset in datasets:
            recipe_dict['dataset'] = dataset
            logger.info(f"Seeking data for dataset: {dataset}")
            for cmip_era in cmip_eras:
                files = _get_timefiltered_files(recipe_dict, exps_list,
                                                cmip_era)

                # assemble in new recipe
                add_datasets = []
                for fn in sorted(files):
                    fn_dir = os.path.dirname(fn)
                    logger.info(f"Data directory: {fn_dir}")
                    out = _file_to_recipe_dataset(fn, cmip_era, recipe_dict)
                    logger.info(f"New recipe entry: {out}", out)
                    if out is None:
                        continue
                    add_datasets.append(out)
                new_datasets.extend(add_datasets)
        additional_datasets[(diag, variable, cmip_era)] = \
            _remove_duplicates(new_datasets)

    # add datasets to recipe as additional_datasets
    shutil.copyfile(input_recipe, output_recipe, follow_symlinks=True)
    _add_datasets_into_recipe(additional_datasets, output_recipe)
    logger.info("Finished recipe filler. Go get some science done now!")


if __name__ == "__main__":
    run()