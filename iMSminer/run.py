# -*- coding: utf-8 -*-
"""
@author: Yu Tin Lin (yutinlin@stanford.edu)
@author: Haohui Bao (susanab20020911@gmail.com)
@author: Boone M. Prentice (booneprentice@ufl.chem.edu)


Please cite the following if iMSminer is helpful to your publication:

    @software{msalign2024,
    author = {Lukasz G. Migas},
    title = {{msalign}: Spectral alignment based on MATLAB's `msalign` function.},
    url = {https://github.com/lukasz-migas/msalign},
    version = {0.2.0},
    year = {2024},
    }
"""

#==============LOAD iMSminer FUNCTIONS================#

import os
os.chdir("/home/yutinlin/workspace/iMSminer")
from data_preprocessing import Preprocess
os.chdir("/home/yutinlin/workspace/iMSminer")
from data_analysis import DataAnalysis
import assorted_functions



#===========PREPROCESSING imzML==============#

preprocess = Preprocess()
preprocess.peak_pick(percent_RAM=5, method="point", generate_spectrum=True)
preprocess.run(percent_RAM=2, peak_alignment=False, align_halfwidth=15, 
               grid_iter_num=10, align_reduce=True, reduce_halfwidth=2, 
               plot_aligned_peak = False, index_peak_plot = 92, plot_num_peaks=10)





#===========ANALYZING PREPROCESSED imzML==============#
data_analysis = DataAnalysis()
data_analysis.load_preprocessed_data()
data_analysis.normalize_pixel(normalization="TIC")
data_analysis.calibrate_mz()
data_analysis.MS1_search()
data_analysis.filter_analytes()
data_analysis.image_clustering(k=10, perplexity=3, zoom=0.3, quantile=95)
data_analysis.insitu_clustering(k=4,perplexity=25, show_ROI=True, show_square=True)
data_analysis.make_FC_plot()
data_analysis.make_boxplot()
data_analysis.get_ion_image(replicate=0, show_ROI=True, show_square=True, color_scheme="inferno")

#p6_10 p6_35 pc_10 pc_35 gf_10 gf_35 cmc_10 cmc_35

