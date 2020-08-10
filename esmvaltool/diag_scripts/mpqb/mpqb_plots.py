"""A collection of plotting functions used in the MPQB diagnostics."""

import datetime
import os

import iris
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import from_levels_and_colors
from matplotlib.ticker import MaxNLocator
import numpy as np
import yaml


def read_mpqb_cfg():
    """Read from mpqb_cfg.yml file."""
    cfg_filename = os.path.join(os.path.split(__file__)[0], 'mpqb_cfg.yml')
    with open(cfg_filename, 'r') as handle:
        mpqb_cfg = yaml.safe_load(handle)
    return mpqb_cfg


def _parse_cmap(plotkwargs):
    """Create a discrete colormap with nice ticks.

    Caveats:
       - Does not work yet for all cases
    """
    # replace the cmap key with the cmap object,
    # and add grey shading for masked values
    # Diverging colorbar centred around zero
    nbins = 11 # has to be uneven

    cmapname = plotkwargs.pop('cmap')
    cmap = matplotlib.cm.get_cmap(cmapname)
    color_list = cmap(np.linspace(0, 1, nbins))
    cmap_name = cmap.name + str(nbins)
    cmap = cmap.from_list(cmap_name, color_list, nbins)

    symmetric = np.abs(plotkwargs['vmin'])==np.abs(plotkwargs['vmax'])

    levels = MaxNLocator(nbins=nbins, symmetric=symmetric).tick_values(
        plotkwargs['vmin'], plotkwargs['vmax'])

    if symmetric:
        print("Deleting middle level")
        # Remove zero from levels
        levels = np.delete(levels, len(levels) / 2)

    color_list = list(color_list)

    cmap, norm = from_levels_and_colors(levels, color_list,
                                        extend=plotkwargs['extend'])
    cmap.set_bad("grey", 0.1)

    plotkwargs['norm'] = norm
    plotkwargs['cmap'] = cmap
    return plotkwargs


def mpqb_mapplot(cube, dataset_cfg, filename, **plotkwargs):
    """Plot maps."""
    fig = plt.figure(dpi=200)
    fig.add_subplot(projection=iris.plot.default_projection(cube))

    datasetnames = read_mpqb_cfg()['datasetnames']
    plottitle = datasetnames[plotkwargs.pop('title')]

    plotkwargs = _parse_cmap(plotkwargs)
    extend = plotkwargs.pop('extend')

    plotkwargs['rasterized'] = True


    pcols = iris.plot.pcolormesh(cube, **plotkwargs)
    # Take out small grid lines like this
    pcols.set_edgecolor('face')
    plt.gca().coastlines()

    # Colorbar
    colorbar = plt.colorbar(pcols, orientation='horizontal', extend=extend)
    colorbar.set_label(cube.units)
    colorbar.ax.tick_params(labelsize=8)

    # Get first entry from all datasets
    sample_dataset = dataset_cfg['input_data'][next(iter(dataset_cfg['input_data']))]
    # Add timeperiod to plot title
    timeperiod = f"{sample_dataset['start_year']}-{sample_dataset['end_year']}"
    plt.title(f"{plottitle} {timeperiod}")
    fig.savefig(filename, bbox_inches='tight')
    plt.close(fig)