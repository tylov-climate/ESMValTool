"""Script to download BerkeleyEarth from its webpage."""

from esmvaltool.cmorizers.data.downloaders.wget import WGetDownloader


def download_dataset(config, dataset, _, __, overwrite):
    """Download dataset.

    Parameters
    ----------
    config : dict
        ESMValTool's user configuration
    dataset : str
        Name of the dataset
    start_date : datetime
        Start of the interval to download
    end_date : datetime
        End of the interval to download
    overwrite : bool
        Overwrite already downloaded files
    """
    downloader = WGetDownloader(
        config=config,
        dataset=dataset,
        overwrite=overwrite,
    )

    downloader.download_file(
        "http://berkeleyearth.lbl.gov/auto/Global/Gridded/"
        "Land_and_Ocean_LatLong1.nc",
        wget_options=[])
