#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Utilities for the calculations of the ETCCDI Climate Change Indices."""
import logging
import os
from pprint import pformat
import numpy as np
import iris
from cf_units import Unit
import esmvalcore.preprocessor
import dask.array as da
import dask.dataframe as dd
import datetime


#from esmvaltool.diag_scripts.shared import (group_metadata, run_diagnostic,
#                                            select_metadata, sorted_metadata)
#from esmvaltool.diag_scripts.shared._base import (
#    ProvenanceLogger, get_diagnostic_filename, get_plot_filename)
#from esmvaltool.diag_scripts.shared.plot import quickplot

logger = logging.getLogger(os.path.basename(__file__))

# aggregation wrapper for iris.Cube.collapsed
agg_wrapper = {
        'full': (lambda cube:
            iris.cube.Cube.collapsed(cube,
                                     'time',
                                     iris.analysis.SUM)),
        'year': (lambda cube:
            iris.cube.Cube.aggregated_by(cube,
                                         'year',
                                         iris.analysis.SUM)),
        }


def __event_count_time__(cube, threshold, logic='less', aggregate='full'):
    """Compute the number of events."""

    # compute the binarized version of the cube
    bin_cube = __boolean_translation__(cube, threshold, logic)

    # aggregate within the aggregate horizon
    res_cube = agg_wrapper[aggregate](bin_cube)

    return res_cube


def __boolean_translation__(cube, threshold, logic='less'):
    """Compute boolean for exceeding threshold of data in cube."""
    logger.info("assessing the logic '{}'".format(logic))

    #test cube against threshold and write into res_cube
    thresh_data = getattr(da, logic)(cube.core_data(),threshold)
    thresh_data = thresh_data.astype(int)

    #copy cube
    res_cube = cube.copy(data = thresh_data)

    return res_cube


def __cumsum_of_boolean__(cube, threshold, logic='less'):
    """Compute cumsum for exceeding threshold of data in cube."""
    # binarize cube
    bin_cube = __boolean_translation__(cube, threshold, logic=logic)

    # calculate cumsum with setback after first 0
    cs_data = __cumsum_with_setback_multiD__(bin_cube.core_data(), 0)
    cs_cube = bin_cube.copy(data=cs_data)

    return cs_cube


def __cumsum_with_setback__(array):
    """Compute cumsum with setback after first 0"""
    cs_not_array = da.logical_not(array).cumsum()
    dataframe = dd.from_dask_array(da.stack([array, cs_not_array],
                                            axis=1),
                                   columns=['data', 'groups'])
    
    cs_df = dataframe.groupby('groups').cumsum()
    return cs_df


def __cumsum_without_setback__(array):
    """Compute cumsum"""
    return array.cumsum(axis=0)


def __cumsum_with_setback_multiD__(arrayMD, axis):
    """Compute cumsum with setback after first 0 along an axis"""
    return da.apply_along_axis(__cumsum_with_setback__,
                               axis, arrayMD,
                               shape=(arrayMD.shape[axis],),
                               dtype=int)


def __cumsum_without_setback_multiD__(arrayMD, axis):
    """Compute cumsum along an axis"""
    return da.apply_along_axis(__cumsum_without_setback__,
                               axis, arrayMD,
                               shape=(arrayMD.shape[axis],),
                               dtype=int)


def threshold_span(cube, threshold_specs, span_specs):
    """Gets the binary for any exceeding of thresholds within the respective span"""

    # cumulative summation of the booleans depending of threshold
    cum_bin = __cumsum_of_boolean__(cube, threshold=threshold_specs['value'], logic=threshold_specs['logic'])

    # boolean translatioon of the cummation depending on span
    span_bin = __boolean_translation__(cum_bin, threshold=span_specs['value'], logic=span_specs['logic'])

    return span_bin


def __first_appearence_data__(data, fill_value):
    """Get the first appearence of True and assign the respective time"""
#    data = da.ma.masked_where(da.logical_not(data), data)

    time_rec = __argmax_wrapper__(data, fill_value)

    time_rec = da.ma.masked_where(time_rec==fill_value, time_rec)

    return time_rec


def __argmax_wrapper__(array, fill_value):
    """wrapper with check on false data"""
    ret = da.argmax(array)
    if not array[ret]:
        ret=fill_value
    
    return ret


def __threshold_over_span__(array, threshold, span, fill_value):
    """find threhsold over span"""
    array_thresh = getattr(da, threshold['logic'])(array, threshold['value'])
    array_sum = __cumsum_with_setback__(array_thresh)
    array_span = getattr(da, span['logic'])(array_sum, span['value'])
    array_span = array_span * 1
    
    return array_span


def __adjust_masked_values__(d1, d2, year_length):
    """adjust for nonending or nonstarting growing seasons"""
    
    # no start scenario
    if da.ma.getmaskarray(d1) and not da.ma.getmaskarray(d2):
        d1 = d2.copy()+1
    # no end scenario
    if da.ma.getmaskarray(d2) and not da.ma.getmaskarray(d1):
        d2 = da.array(year_length)
        
    return d1, d2


def __gsl_per_year_per_ts__(array, split_pnt, specs, fill_value):
    """calculate the growing season per pixel for a yearly timeseries"""
    
    start_span = __threshold_over_span__(array[:split_pnt], specs['start']['threshold'], specs['start']['span'], fill_value).to_dask_array(lengths=True).squeeze()
    end_span = __threshold_over_span__(array[split_pnt:], specs['end']['threshold'], specs['end']['span'], fill_value).to_dask_array(lengths=True).squeeze()

    # first appearence with shift to first day of first appearence
    first_appearence = __first_appearence_data__(start_span, fill_value)+2-specs['start']['span']['value']
    
    end_span_cumusm = __cumsum_without_setback__(end_span)
    
    # first appearence with shift to first day of first appearence
    last_appearence = __first_appearence_data__(end_span_cumusm, fill_value)+2+split_pnt#-span_end['value']
    
    first_appearence, last_appearence = __adjust_masked_values__(first_appearence, last_appearence, len(array))
    
    res = last_appearence - first_appearence + 1
    
    #TODO: decide on tradeoff between memory and processing time
#    return res 
    return res.compute()
    

def __gsl_applied_MD__(array_MD, axis, split_pnt, specs, fill_value):
    """calculate the growing season for a multipixel yearly timeseries"""
    
    yearly_gsl = da.apply_along_axis(__gsl_per_year_per_ts__, axis, array_MD, split_pnt, specs, fill_value, shape = (1,), dtype = int)
    
    return da.reshape(yearly_gsl, yearly_gsl.shape[0:2])


def merge_SH_NH_cubes(loCubes, fill_value = -9999):
    """merges a list of 2 cubes (northern and southern hemisphere) with overlaping timeseries"""
    which_longer = np.argmax([lC.shape[0] for lC in loCubes])
    
    # search the longer time series
    longer = loCubes.pop(which_longer)
    shorter = loCubes[0]
    
    # adjust time coordinate and attach missing year segments
    longer_time = longer.coord('year')
    shorter_time = shorter.coord('year')
    
    longer_time_pts = longer_time.points
    shorter_time_pts = shorter_time.points
    
    add_segments = longer_time[[i for i,ltp in enumerate(longer_time_pts) if not ltp in shorter_time_pts]]
    
    additional_segments = [shorter]
    for i in add_segments:
        loc_slice = shorter[0,:,:].copy() * 0 + fill_value
        loc_slice.metadata = shorter.metadata
        loc_slice.replace_coord(i)
        loc_slice = iris.util.new_axis(loc_slice, 'year')
        additional_segments.append(loc_slice)
        
    shorter = iris.cube.CubeList(additional_segments).concatenate_cube()
    
    # adjust latitude coordinate and delete duplicate latitude segments
    longer_lat = longer.coord('latitude')
    shorter_lat = shorter.coord('latitude')
    
    longer_lat_pts = longer_lat.points
    shorter_lat_pts = shorter_lat.points
    
    del_segments_ind = [i for i,llp in enumerate(longer_lat_pts) if llp in shorter_lat_pts]
    
    
    if len(del_segments_ind) > 0:
        shorter = shorter.extract(iris.Constraint(latitude = lambda cell: cell not in del_segments_ind))
    
    return iris.cube.CubeList([shorter, longer]).concatenate_cube()


def gsl_aggregator(cube, specs):
    """ calculate the growing season for a cube per year"""
    GSL = iris.analysis.Aggregator('gsl_aggregator',
                                    __gsl_applied_MD__,
                                    lazy_func=__gsl_applied_MD__
                                    )
    
    if 'mon' not in [cc.long_name for cc in cube.coords()]:
        iris.coord_categorisation.add_month_number(cube, 'time', name='mon')
    
    res_cubes = []
    
    for years in np.unique(cube.coord('year').points):
        loc_cube = cube.extract(iris.Constraint(year = lambda cell: cell == years))
        months = loc_cube.coord('mon').points
        if np.all([ m in months for m in np.arange(1,13)]):
            mid_mon = months[0] + specs['end']['time']['delay'] - 1
            mid_mon_index = [i for i, e in enumerate(months) if e == mid_mon][-1]
            res_cube = loc_cube.collapsed('year', GSL, split_pnt=mid_mon_index, specs=specs, fill_value=-9999)
            res_cube.remove_coord('time')
            res_cube.remove_coord('mon')
            res_cubes.append(res_cube)
    
    return iris.cube.CubeList(res_cubes).merge_cube()
    

def __numdaysyear_base__(cube, threshold=273.15, logic='less'):
    """Compute number of days per year for specific logic and threshold"""
    # set aggregation level
    agg = 'year'

    # add year auxiliary coordinate
    if agg not in [cc.long_name for cc in cube.coords()]:
        iris.coord_categorisation.add_year(cube, 'time', name=agg)

    # calculate event count
    res_cube = __event_count_time__(cube, threshold, logic=logic, aggregate=agg)

    return res_cube


def numdaysyear_wrapper(cubes, specs):
    """Wrapper function for several number of days per year indices"""
    
    # test if required variable is in cubes
    fdcube = None
    for var, cube in cubes.items():
        if var in specs['required']:
            fdcube = cube

    if fdcube is None:
        logger.error('Cannot calculate number of {} for any of the following variables: {}'.format(
                specs['name'], cubes.keys()))
        return

    logger.info('Computing yearly number of {}.'.format(specs['name']))

    # get cube unit
    c_unit = fdcube.units
    logger.info("The cube's unit is {}.".format(c_unit))

    # get threshold
    threshold = __adjust_threshold__(specs['threshold'], c_unit)
    
    logger.info('Threshold is {} {}.'.format(threshold, c_unit))

    # compute index
    res_cube = __numdaysyear_base__(fdcube,
                                    threshold,
                                    logic=specs['threshold']['logic'])

    # adjust variable name and unit
    res_cube.rename(specs['cf_name'])
    res_cube.units = Unit('days per year')

    return res_cube


def __adjust_threshold__(specs_threshold, unit):
    """adjusts a threshold to the requested unit (if possible) and returns value only"""
    threshold = specs_threshold['value']
    
    # convert depending on unit
    if not unit == specs_threshold['unit']:
        threshold = Unit(specs_threshold['unit']).convert(threshold, unit)
        
    return threshold


def convert_ETCCDI_units(cube):
    """Converting cubes' units if necessary"""
    if cube.standard_name == 'precipitation_flux' and cube.units == 'kg m-2 s-1':
        cube.data = cube.data * 24. * 3600.
        cube.units = Unit('mm day-1')
    return cube


def select_value(alias_cubes, specs):
    """Select value per period according to given logical operator."""
    logger.info(f"Computing the {specs['cf_name'].replace('_',' ')}.")
    _check_required_variables(specs['required'], [item.var_name for _,item in alias_cubes.items()])
    # Here we assume that only one variable is required.
    cube = [item for _,item in alias_cubes.items() if item.var_name in specs['required']].pop()
    if 'period' not in specs.keys():
        raise Exception(f"Period needs to be specified.")
    statistic_function = getattr(esmvalcore.preprocessor, f"{specs['period']}_statistics", None)
    if statistic_function:
        result_cube = statistic_function(cube, specs['logic'])
    else:
        raise Exception(f"Period {specs['period']} not implemented.")
    result_cube.rename(specs['cf_name'])
    return result_cube

def _check_required_variables(required, available):
    missing = [item for item in required if item not in available]
    if len(missing):
        raise Exception(f"Missing required variable {' and '.join(missing)}.")

def regional_constraint_from_specs(spec):
    ###DOES NOT WORK###
    """Converting regional specs into iris constraints"""
    constraints = {}
    for st_set, st_def in spec['spatial_subsets'].items():
        logger.info(st_set)
        constraints[st_set] = iris.Constraint(
                latitude = lambda cell: np.min(st_def['latitude']) <= cell <= np.max(st_def['latitude']), 
                longitude = lambda cell: np.min(st_def['longitude']) <= cell <= np.max(st_def['longitude']),
                )
        logger.info(np.min(st_def['latitude']))
        logger.info(np.max(st_def['latitude']))
    logger.info(constraints)
    return constraints


def __nonzero_mod__(x, mod):
    """Compute modulo without 0 (e.g. for months)"""
    res = x%mod
    if res == 0:
        return mod
    else:
        return res


def gsl_check_units(cubes, specs):
    """Check the units in specs for gsl conformity"""

    if len(specs['required'])>1:
        logger.error('Searching too many cubes (should be one):'.format(
                specs['required']))
        raise Exception(f'Wrong data.')

    if specs['required'][0] not in cubes.keys():
        logger.error('Required cube not available. Looking for {}.'.format(
                specs['required'][0]))
        raise Exception(f'Wrong data.')
    else:
        c_unit = cubes[specs['required'][0]].units

    if not cubes[specs['required'][0]].attributes['frequency'] == Unit('day'):
        logger.error('Requires cube frequency in days, got {}.'.format(
                cubes[specs['required'][0]].attributes['frequency']))
        raise Exception(f'Wrong data.')
        

    for season in ['start', 'end']:
        specs[season]['threshold']['value'] = __adjust_threshold__(
                specs['start']['threshold'], c_unit)
        specs[season]['threshold']['unit'] =  c_unit
        if not specs[season]['span']['unit'] == Unit('day'):
            logger.error('Requires span in days, got {}.'.format(
                    specs[season]['span']))
            raise Exception(f'Wrong specifications.')
        if not specs[season]['time']['unit'] == Unit('month'):
            logger.error('Requires time in months, got {}.'.format(
                    specs[season]['time']))
            raise Exception(f'Wrong specifications.')

    return specs