import copy

import torch
import torch.nn as nn
import torch.nn.functional as F
import os
import json
from torch.utils.data import DataLoader
from algorithms.edges.edgebase import Edgebase
from algorithms.optimizers.optimizer import *
from algorithms.edges.nn_utils import hessian

# Implementation for Conjugate gradient method clients

class edgeGT(Edgebase):
    def __init__(self, numeric_id, train_data, test_data, model, batch_size, learning_rate, alpha, eta, L,
                 local_epochs, optimizer):
        super().__init__(numeric_id, train_data, test_data, model[0], batch_size, learning_rate, alpha, eta, L,
                         local_epochs)

        if (model[1] == "linear_regression"):
            self.loss = nn.MSELoss()
        elif model[1] == "logistic_regression":
            self.loss = nn.BCELoss()
        else:
            self.loss = nn.NLLLoss()

        self.optimizer = torch.optim.SGD(self.model.parameters(), lr=self.learning_rate)
        self.hessian = None

    def set_grads(self, new_grads):
        if isinstance(new_grads, nn.Parameter):
            for model_grad, new_grad in zip(self.server_grad, new_grads):
                model_grad.grad = new_grad.data.clone()
        elif isinstance(new_grads, list):
            for idx, model_grad in enumerate(self.server_grad):
                model_grad.grad = new_grads[idx].clone()

    def get_full_grad(self):
        for X, y in self.trainloaderfull:
            self.model.zero_grad()
            output = self.model(X)
            loss = self.loss(output, y)
            loss.backward()

    def train(self, epochs, glob_iter):
        self.model.zero_grad()

        # Sample a mini-batch (D_i)
        (X, y) = self.get_next_train_batch()
        loss = self.total_loss(X=X, y=y, full_batch=False, regularize=True)
        loss.backward(create_graph=True)
        
        
        for param, d in zip(self.model.parameters(), self.dt):
            # Set direction to 0 at the begining
            d.data = - 0 * param.grad.data.clone()
        
        # matrices should be flatten
        grads = torch.cat([x.grad.data.view(-1) for x in self.server_grad]).reshape(-1,1)
        dt = torch.cat([x.data.view(-1) for x in self.dt]).reshape(-1,1)
        
        # calculating hessian
        client_grads = torch.autograd.grad(loss, self.model.parameters(), create_graph=True, retain_graph=True)
        hess = hessian(client_grads, self.model)
        
        I =  torch.eye(*hess.size())
        hess = hess + self.alpha * I # adding identify notice for regularization

        
        # conjugate gradien initials 
        tol = 1e-8 # threshold for stopping cg iterations
        r = torch.mm(hess,dt) - grads
        p = r.clone().detach()
        rsold = torch.dot(r.view(-1), r.view(-1))
        
        # conjugate gradien iteration
        for i in range(1, self.local_epochs + 1):  # R
            hess_p = torch.mm(hess,p)
            alpha  = rsold /torch.dot(p.view(-1),hess_p.view(-1))
            dt.data = dt.data + alpha * p
            r.data = r - alpha* hess_p
            rsnew = torch.dot(r.view(-1), r.view(-1))
            if np.sqrt(rsnew.detach().numpy()) < tol:
                #print('Itr:', i)
                break
            else:
                p.data = r + (rsnew / rsold)* p
                rsold.data = rsnew
        
        # coppying rsult to self.dt
        index=0
        for d in self.dt:
            shape = d.data.shape
            d.data = dt[index: index+ d.data.numel()].reshape(shape)
            index = index+ d.data.numel()